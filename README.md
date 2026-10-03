# chatplus-core

실행 중인 채팅+ Windows 클라이언트의 **로컬 캐시를 읽는 코어와 HTTP API**입니다.
tsuba와 별도 저장소이며 TUI는 없습니다. 현재 제공 기능은 방 목록과 캐시된 메시지 조회입니다.
발송, SSE, 첨부파일 다운로드, saya 연결은 아직 구현하지 않았습니다.

## 실행

Python 3.11 이상과 uv가 필요합니다. 채팅+ PC에서 먼저 정상 로그인해야 합니다.
현재 Windows 사용자의 로컬 저장소만 읽으며 다른 계정에 로그인하지 않습니다.

```powershell
cd C:\Users\USER\WebstormProjects\chatplus-core
uv sync --frozen
$env:CHATPLUS_TOKEN = uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
uv run python -m chatplus_core
```

기본 주소는 `http://127.0.0.1:8766`입니다. 포트를 바꾸려면 `CHATPLUS_PORT`
환경 변수에 1024~65535 범위의 정수를 지정합니다(범위 밖이거나 숫자가 아니면 시작하지 않습니다).
서버는 루프백에만 바인딩하며, 32바이트 미만 토큰으로는 시작하지 않습니다.
실행 시 생성한 토큰을 클라이언트에도 설정하세요.
요청 접근 로그는 끕니다. 저장소 키와 메시지를 별도 파일로 내보내지 않습니다.

```powershell
curl.exe -H "Authorization: Bearer $env:CHATPLUS_TOKEN" http://127.0.0.1:8766/capabilities
curl.exe -H "Authorization: Bearer $env:CHATPLUS_TOKEN" "http://127.0.0.1:8766/rooms?limit=100"
```

`/rooms` 응답의 `id`를 `/messages?roomId=<id>&limit=100`에 전달합니다.
OpenAPI는 인증된 `GET /openapi.json`으로 조회합니다.
모든 구현된 라우트에 Bearer 인증이 필요하며, 토큰의 해시를 상수 시간으로 비교합니다.

| 라우트 | 결과 |
| --- | --- |
| `GET /capabilities` | `rooms.read`, `messages.read`만 선언 |
| `GET /rooms?limit=100` | 방 ID·캐시된 이름, 전체 캐시 방 수, 잘림 여부 |
| `GET /messages?roomId=…&limit=100` | 지정 방의 최근 캐시 시간 구간 |
| `GET /openapi.json` | 현재 실행 코드에서 생성한 별도 API 계약 |

`limit`는 1~1000입니다. 메시지는 `sentAt`, `id` 오름차순이며,
그 순서에서 마지막 `limit`개를 반환합니다. 이는 인과 순서나 서버 수신 순서가 아닙니다.
`text: null`은 읽을 수 있는 `TextContent`가 없다는 뜻입니다.
XML·리치 카드·첨부파일을 임의의 평문으로 변환하지 않습니다.
`directionRaw`는 원본의 방향 문자열이며 검증되지 않은 의미로 정규화하지 않습니다.

## 보장 범위

- `coverage: "local-cache"`만 주장합니다. 휴대폰이나 서버의 전체 이력이 아닙니다.
- 응답의 `total`은 이번 캐시 읽기에서 해당하는 레코드 수입니다.
  `truncated: true`는 요청 한도로 잘렸다는 뜻이며, 완전한 이력이 있다는 뜻이 아닙니다.
- 방 ID는 캐시의 방 resource URL에 대한 SHA-256입니다. 원본 URL은 API에 노출하지 않습니다.
- Hive 프레임을 순서대로 재생하여 같은 키의 교체와 삭제를 반영합니다.
  CRC·키·길이·패딩이 맞지 않거나 지원하지 않는 값 형식이면 전체 요청을 거절합니다.
- 키 파일은 AES-GCM 인증을 확인합니다. **Hive 데이터는 AES-CBC와 CRC이며,
  CRC는 악의적인 변조에 대한 인증이 아닙니다.**
- 파일별 최대 64 MiB, 값 길이/컬렉션 원소 수 최대 1,000,000, 중첩 깊이 최대 32입니다.
  응답 개수 한도는 파일을 일부만 읽는다는 뜻이 아닙니다.
- 읽는 중 관측된 파일 변경과 방-메시지 연결 불일치는 거절합니다.
  반복 읽기 일치는 여러 파일의 원자적 스냅샷이나 ABA 방지를 증명하지 않습니다.
- 프로필 또는 대응 키가 여러 개이면 임의로 첫 항목을 고르지 않습니다.
- 읽기는 클라이언트 창을 열거나 읽음 상태를 바꾸지 않습니다.
  RCS 프로토콜, 로그인, 토큰 갱신을 재구현하지 않습니다.

잘못된 쿼리는 400, 인증 실패는 401, 없는 방은 404,
로컬 환경/동시 변경 문제는 503 `STORAGE_UNAVAILABLE`,
지원하지 않거나 손상된 형식은 503 `UNSUPPORTED_FORMAT`으로 답합니다.
자동 재시도는 하지 않습니다.

## 검증

```powershell
uv run basedpyright
uv run ruff check chatplus_core tests tools
uv run ruff format --check chatplus_core tests tools
uv run python -m pytest -q
uv run python -m tools.verify_live
uv run python -m tools.verify_socket
```

합성 테스트는 실제 계정 자료를 사용하지 않습니다.
`tools.verify_live`는 실제 로컬 파일을 API에 연결하지만 방·본문·키 대신 개수만 출력합니다.
`tools.verify_socket`은 비어 있는 루프백 포트를 직접 골라 `CHATPLUS_PORT`로
실제 서버를 별도 프로세스로 띄우고, 그 포트의 준비 로그를 기다린 뒤 TCP로
무인증 거절(401)과 인증 조회(200)를 확인한 다음 자신이 띄운 프로세스만 종료합니다.

2026-10-03 확인한 클라이언트는 `com.sktelecom.torchlight` 1.5.4.6입니다.
77개 합성 테스트가 통과했고, 실제 캐시에서 43개 방·140개 메시지를 조회했으며
그중 109개 메시지에 `TextContent`가 있었습니다. 실제 루프백 TCP 서버로도
무인증 401, 인증 200, 방 43개 조회를 확인했습니다. 테스트 메시지는 보내지 않았습니다.
다른 클라이언트 버전에 대한 호환성은 아직 입증하지 않았습니다.

## 후속 작업

상세 의존성, 불변식, 반례, 실행 절차와 완료 조건은
[후속 작업 계획](.omo/plans/chatplus-core-followup.md)에 정리합니다.
현재 API는 tsuba 계약의 구현이나 saya 연결 완료를 뜻하지 않습니다.

공식 PC 지원 조건과 외부 형식 근거:

- https://chattingplus.co.kr/down
- https://chattingplus.co.kr/faq
- https://raw.githubusercontent.com/isar/hive/v2.2.3/hive/lib/src/binary/binary_reader_impl.dart
- https://raw.githubusercontent.com/isar/hive/v2.2.3/hive/lib/src/crypto/hive_aes_cipher.dart
- https://raw.githubusercontent.com/juliansteenbakker/flutter_secure_storage/develop/flutter_secure_storage_windows/windows/flutter_secure_storage_windows_plugin.cpp

공식 안내는 SK텔레콤 가입자와 One UI 6.0 이상인 SKT향/삼성 자급제 갤럭시를
PC 지원 대상으로 안내합니다. 개인 계정용 공개 API/SDK 문서는 확인하지 못했으며,
없다는 사실을 증명한 것은 아닙니다. kanao의 로컬 읽기/클라이언트 입력 분리를
참고했지만, 카카오톡의 DB·암호화 가정을 재사용하지 않았습니다.
