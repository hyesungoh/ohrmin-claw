"""NL cron 스케줄러 — 의존성 없는 5필드 cron 매처 + 상대 one-shot + 원자적 JSON 스토어.

CronStore는 경로 주입 가능(모듈 레벨 하드코딩 경로 없음, tmp_path 테스트 대응)하며 원자적
temp+rename으로 data/cron_jobs.json에 영속한다. schema_version 마커를 포함한다.

매처는 croniter 등 외부 의존성 없이 stdlib만으로 5필드 cron(`m h dom mon dow`)과 상대
one-shot(`30m`/`2h`/`1d`)을 지원한다.

시간대 가정: 모든 스케줄은 호출자가 넘긴 `now`의 타임존(로컬 aware)에서 벽시계 기준으로
평가된다. compute_next_run/due는 wall-clock 분 단위로 후보를 증가시키므로, DST가 있는 존에서는
전환 경계에서 분이 건너뛰거나 반복될 수 있다 — 배포 타임존(KST)은 DST가 없어 정확하다.

테스트 지원: 매처 함수(due/compute_next_run)는 wall-clock을 직접 호출하지 않고 `now`를
인자로 받으므로 프리즈드 클록으로 결정적 테스트가 가능하다.
"""
# CronStore.list() 메서드가 클래스 네임스페이스의 builtin `list`를 가리므로, 이후 메서드의
# `list[dict]` 애노테이션이 def-time에 깨진다 → 애노테이션 지연 평가로 회피.
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
import uuid

# 스토어 스키마 버전 마커. 잡 shape 변경 시 마이그레이션 참조.
SCHEMA_VERSION = 1

# 상대 one-shot 표현식: 30m / 2h / 1d.
_RELATIVE_RE = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")

# cron 5필드의 (하한, 상한). dow는 0-7 파싱 후 7→0 정규화(일요일 겸용).
_CRON_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def is_relative(schedule: str) -> bool:
    """스케줄이 상대 one-shot(30m/2h/1d) 표현식인지."""
    return bool(_RELATIVE_RE.match(schedule or ""))


def _relative_delta(schedule: str) -> datetime.timedelta:
    m = _RELATIVE_RE.match(schedule)
    if not m:
        raise ValueError(f"상대 표현식이 아님: {schedule!r}")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError("상대 시간은 양수여야 합니다.")
    if unit == "m":
        return datetime.timedelta(minutes=n)
    if unit == "h":
        return datetime.timedelta(hours=n)
    return datetime.timedelta(days=n)


def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    """cron 필드 하나(`*`, `1,3`, `1-5`, `*/2`, `10-20/2`)를 허용값 집합으로 파싱."""
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"빈 cron 필드 요소: {spec!r}")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"step은 양수여야 합니다: {part!r}")
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start_s, end_s = base.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(base)
        if start > end:
            raise ValueError(f"범위 시작이 끝보다 큼: {part!r}")
        for v in range(start, end + 1, step):
            if not (lo <= v <= hi):
                raise ValueError(f"필드 값 {v}가 범위 [{lo},{hi}] 밖: {spec!r}")
            values.add(v)
    return values


def _parse_cron(schedule: str) -> list[tuple[set[int], bool]]:
    """5필드 cron을 (허용값 집합, is_wildcard) 리스트로 파싱.

    is_wildcard 플래그는 dom/dow 상호작용(둘 다 제한 시 OR)을 구현하기 위해 보존한다.
    잘못된 형식이면 ValueError.
    """
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError(f"cron은 5개 필드여야 합니다 (받음: {len(fields)}): {schedule!r}")
    parsed = []
    for spec, (lo, hi) in zip(fields, _CRON_BOUNDS):
        values = _parse_field(spec, lo, hi)
        parsed.append((values, spec.strip() == "*"))
    # dow 7(일요일 겸용)을 0으로 정규화.
    dow_values, dow_wild = parsed[4]
    if 7 in dow_values:
        dow_values = (dow_values - {7}) | {0}
        parsed[4] = (dow_values, dow_wild)
    return parsed


def _cron_matches(parsed: list[tuple[set[int], bool]], dt: datetime.datetime) -> bool:
    """파싱된 cron 필드가 datetime(분 단위)에 매치되는지.

    dom/dow가 둘 다 제한적이면 Vixie cron 표준대로 OR(둘 중 하나 매치), 아니면 AND.
    """
    (minute, _), (hour, _), (dom, dom_wild), (mon, _), (dow, dow_wild) = parsed
    if dt.minute not in minute:
        return False
    if dt.hour not in hour:
        return False
    if dt.month not in mon:
        return False
    # cron dow: 0=일요일..6=토요일. Python isoweekday: 월1..일7 → %7로 일0..토6 변환.
    py_dow = dt.isoweekday() % 7
    dom_ok = dt.day in dom
    dow_ok = py_dow in dow
    if not dom_wild and not dow_wild:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def validate_schedule(schedule: str) -> None:
    """스케줄 문자열이 상대 표현식이거나 유효한 5필드 cron인지 검증. 아니면 ValueError."""
    schedule = (schedule or "").strip()
    if not schedule:
        raise ValueError("schedule이 비어 있습니다.")
    if is_relative(schedule):
        _relative_delta(schedule)  # 양수 검증
        return
    _parse_cron(schedule)  # 형식 검증


def compute_next_run(job: dict, now: datetime.datetime) -> datetime.datetime:
    """잡의 다음 실행 시각을 계산.

    - 상대 one-shot: now + delta (단발).
    - cron: now보다 '엄격히 이후'인 가장 이른 매치 분. 최대 366일 탐색.

    now는 tz-aware를 권장(로컬). 반환값은 now의 tzinfo를 그대로 이어받는다.
    """
    schedule = job["schedule"].strip()
    if is_relative(schedule):
        return now + _relative_delta(schedule)
    parsed = _parse_cron(schedule)
    # 다음 분 경계부터 탐색(now의 초/마이크로초 절삭 → 항상 now보다 이후).
    candidate = (now + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = candidate + datetime.timedelta(days=366)
    while candidate <= limit:
        if _cron_matches(parsed, candidate):
            return candidate
        candidate += datetime.timedelta(minutes=1)
    raise ValueError(f"충족 불가능한 cron 스케줄: {schedule!r}")


def due(job: dict, now: datetime.datetime) -> bool:
    """잡이 지금 실행 대상인지 — 비-paused 이고 now >= next_run_iso."""
    if job.get("paused"):
        return False
    nr = job.get("next_run_iso")
    if not nr:
        return False
    try:
        next_run = datetime.datetime.fromisoformat(nr)
    except (ValueError, TypeError):
        return False
    try:
        return now >= next_run
    except TypeError:
        # naive/aware 불일치(손상된 잡) — 비교 불가 시 due 아님으로 처리해 tick 스톨을 막는다.
        return False


class CronStore:
    """cron 잡을 원자적 JSON 파일에 영속하고 CRUD한다 (경로 주입 가능, 재시작 생존).

    잡 shape: {id, prompt, schedule, deliver_channel_id, next_run_iso, last_run_iso,
    paused, max_turns}.
    """

    def __init__(self, path: str):
        self.path = path
        self._jobs: dict[str, dict] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            for job in data.get("jobs", []):
                if isinstance(job, dict) and job.get("id"):
                    self._jobs[job["id"]] = job
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ cron 스토어 로드 실패({self.path}): {e}")

    def _persist(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = {"schema_version": SCHEMA_VERSION, "jobs": list(self._jobs.values())}
        # 원자적 temp+rename — 부분 쓰기로 스토어가 손상되지 않게 한다.
        fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".cron_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def create(
        self,
        prompt: str,
        schedule: str,
        now: datetime.datetime,
        *,
        deliver_channel_id=None,
        max_turns: int = 15,
        job_id: str | None = None,
    ) -> dict:
        """잡 생성. schedule 검증(ValueError 전파) 후 next_run 계산 + 영속."""
        schedule = schedule.strip()
        validate_schedule(schedule)
        job_id = job_id or uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "prompt": prompt,
            "schedule": schedule,
            "deliver_channel_id": deliver_channel_id,
            "next_run_iso": compute_next_run({"schedule": schedule}, now).isoformat(),
            "last_run_iso": None,
            "paused": False,
            "max_turns": int(max_turns),
        }
        self._jobs[job_id] = job
        self._persist()
        return job

    def list(self) -> list[dict]:
        return list(self._jobs.values())

    def count(self) -> int:
        return len(self._jobs)

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> dict | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.update(fields)
        self._persist()
        return job

    def pause(self, job_id: str) -> dict | None:
        return self.update(job_id, paused=True)

    def resume(self, job_id: str) -> dict | None:
        return self.update(job_id, paused=False)

    def remove(self, job_id: str) -> dict | None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            self._persist()
        return job

    def due_jobs(self, now: datetime.datetime) -> list[dict]:
        """지금 실행 대상(비-paused + due)인 잡 목록."""
        return [j for j in self._jobs.values() if due(j, now)]

    def mark_fired(self, job_id: str, now: datetime.datetime) -> dict | None:
        """잡 발화 후 상태 갱신.

        상대 one-shot은 자기 삭제(반환 None), cron은 last_run 기록 + next_run 재계산.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if is_relative(job["schedule"]):
            self._jobs.pop(job_id, None)
            self._persist()
            return None
        job["last_run_iso"] = now.isoformat()
        job["next_run_iso"] = compute_next_run(job, now).isoformat()
        self._persist()
        return job
