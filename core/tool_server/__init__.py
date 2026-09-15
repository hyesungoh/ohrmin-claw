"""공유 도구 계층 — 백엔드 중립 ServerSpec · strict JSON Schema · CallerCapability · transport 2종.

- spec.py: ToolSpec/ServerSpec/ParamSpec + @tool 데코레이터 (도구 모듈이 사용)
- schema.py: strict JSON Schema 변환 + 이름 규칙 검증
- capability.py: priv/ro 서버측 권한 래퍼 (mutation MCP 차단 + None 인자 제거)
- claude_sdk_bridge.py: Claude = SDK 인프로세스 서버 (claude_agent_sdk import는 여기와 core/llm.py만)
- http_server.py: Codex/Grok = 봇 루프 내 streamable HTTP MCP 서버 (127.0.0.1, 경로 토큰)
"""
