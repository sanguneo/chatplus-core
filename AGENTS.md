# PROJECT KNOWLEDGE BASE

**Generated:** 2026-10-06
**Commit:** 1a3a49f (v0.9.0, macOS 지원)
**Branch:** master

## OVERVIEW
실행 중인 채팅+ PC 클라이언트(Windows·macOS)의 암호화된 로컬 Hive 캐시를 읽어 tsuba 어댑터 계약
(`contract/tsuba.openapi.json`, 0.2.7)으로 답하는 FastAPI 서비스. 보내기는 클라이언트 창을 사람처럼 조작한다.
Python 3.11+, uv, FastAPI, cryptography, ctypes(Win32 / Accessibility).

## STRUCTURE
```
chatplus-core/
├── chatplus_core/
│   ├── storage.py     # 프로필·Hive 키 찾기: windows_profile() / mac_profile() / discover()
│   ├── hive.py        # Hive v2 프레임 재생 + AES-CBC/CRC 검증
│   ├── values.py      # Hive 어댑터 직렬화 → Record (모르는 형식은 전체 거절)
│   ├── reader.py      # 스냅샷 → 방·메시지, 이름·번호 표시
│   ├── api.py         # 라우트·인증·SSE·/send
│   ├── upload.py      # /upload 폼·스테이징·되읽기
│   ├── sender.py      # 보내기 오류·WriteLine·confirm (플랫폼 무관)
│   ├── winclient.py   # Windows: Win32 창 메시지 + Windows OCR
│   └── macclient.py   # macOS: AX + CGEventPostToPid, OCR 없음
├── contract/          # tsuba 계약 바이트 복사본 (고정 해시)
├── tests/             # 합성 Hive 픽스처만, 실데이터 없음
└── tools/             # verify_live / verify_socket (실제 캐시·실제 소켓)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| 새 Hive 값 형식 | `values.py` `ADAPTERS`, `ValueReader` | 모르는 typeId는 거절 유지 |
| 방·메시지 매핑 | `reader.py` `Reader.snapshot`, `to_message`, `to_room` | 방 ID = SHA-256(프로필 이름 ‖ resource URL) |
| 키·프로필 위치 | `storage.py` | Windows `.secure`+자격 증명, mac plist `flutter.cipher_key_*` |
| 오류 코드·상태 | `envelope.py` `ErrorCode`, `STATUS` | 계약과 1:1, `test_contract.py`가 고정 |
| 보내기 흐름 | `api.perform_send`, `upload.perform_upload` | 둘 다 `WriteLine` + 캐시 되읽기 |
| 창 조작 | `winclient.ClientWindow` / `macclient.MacClient` | 같은 `TextWindow`·`Uploader` 프로토콜 |
| 플랫폼 선택 | `__main__.writers`, `storage.discover` | `platform.system()` |
| 계약 올리기 | `contract/`, `api.CONTRACT_VERSION`, `test_contract.py` 고정값 | 셋을 함께 |

## CODE MAP
| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `Reader` | class | reader.py | 72 / 12 files | 매 요청 캐시 스냅샷, 방·메시지·번호 |
| `create_app` | fn | api.py | 32 / 8 | FastAPI 조립, 기능 신고 |
| `ErrorCode` | enum | envelope.py | 66 / 8 | 계약 오류 코드 |
| `decode_frames` | fn | hive.py | 23 / 4 | Hive 프레임 재생 |
| `Profile` | dataclass | storage.py | 22 / 6 | 프로필 폴더 + 메모리 키 |
| `discover` | fn | storage.py | 4 / 2 | 플랫폼별 프로필 찾기 |
| `WindowSender` | class | winclient.py | 7 / 3 | 방 → 번호 → 창 (양 플랫폼 공용) |
| `WriteLine`, `confirm` | class, fn | sender.py | 11 / 4–5 | 쓰기 한 줄, 되읽기 |
| `frame`, `encode`, `profile_at` | test fns | tests/test_hive.py, tests/fixtures.py | 122 / 45 / 32 | 합성 Hive 박스 만들기 |

## CONVENTIONS
- ruff `select = ["ALL"]`, 줄 100. 공개 함수·클래스는 docstring 필수, 비공개 헬퍼는 생략.
- basedpyright `typeCheckingMode = "all"`, `pythonPlatform = "Windows"` 고정. 한 번의 검사로 양 플랫폼 코드를 다 본다.
- 플랫폼 분기는 **실행 시점 `platform.system()`**. `sys.platform` 분기는 반대쪽을 `reportUnreachable`로 잡는다.
- 플랫폼 전용 모듈도 어디서나 import되게 둔다(`ctypes.WinDLL`은 주석에 쓰지 않고 `ctypes.CDLL`). 테스트는 양쪽에서 다 돈다.
- ctypes 호출은 `_bind`로 프로토타입을 선언하고 `Callable[..., int]`로 cast(Any 금지).
- 응답에 해당 없는 키는 `null`이 아니라 생략(`response_model_exclude_none`).
- 오류 메시지에 경로·번호·계정·키를 넣지 않는다(`StorageError`는 이유만).
- 파일 첫 줄 `# Copyright (c) 2026 chatplus-core contributors`.

## ANTI-PATTERNS (THIS PROJECT)
- 자동 재시도 금지: submitted ≠ delivered. 되읽기 실패는 `INTERNAL`("submitted but not confirmed").
- 확인 안 된 방으로 보내기 금지: 번호가 정확히 한 결과와 숫자 그대로 같을 때만 연다. 미전송 초안·그림이 있으면 `FORBIDDEN`.
- 프로필·키가 둘 이상이면 고르지 말고 거절. 모르는 Hive 형식도 일부만 읽지 말고 전체 거절.
- 키·메시지를 파일로 내보내거나 로그에 남기지 않는다. 접근 로그는 꺼져 있다.
- 실데이터(방 이름, 본문, 번호)를 테스트·이슈·커밋·도구 출력에 넣지 않는다. `verify_live`는 개수만.
- 프로토콜 재구현·로그인·토큰 갱신 금지. 실행 중인 공식 클라이언트의 캐시와 창만 쓴다.
- 첨부는 이미지(jpeg/png/gif)만. 영상·파일 보내기는 범위 밖(owner 결정).

## UNIQUE STYLES
- 창 조작은 측정한 768×680 배치 기준 좌표(Windows `CLIENT_SIZE`, mac 상대 프레임 상자). 다른 크기면 맞췄다가 되돌린다.
- mac: Flutter 입력칸은 AX `AXValue` 쓰기를 무시 → 글자는 `CGEventPostToPid`. ⌘ 단축키는 비활성 앱에 안 간다.
- mac: 방이 열리면 목록이 가려진다. 머리글 단추(364,42)는 창을 목록 화면으로 접는다 → 접힘을 기다린 뒤 768로 다시 편다.
- mac: 열기 시트 제목은 `AXDescription`에 있고, 그림은 `~/Downloads/chatplus-core-uploads/`에서 `AXOpen`으로 고른다.

## 한 플랫폼에서만 작업할 때
- 공용 파일(`CONTRIBUTING.md` 표), `contract/`, 오류 코드, 진입점을 바꾸면 이슈 템플릿 `check-windows` / `check-macos`로
  다른 플랫폼 확인을 요청한다(라벨 `check: windows`, `check: macos`). **권고일 뿐 병합을 막지 않는다.**
- 플랫폼 전용 파일만 바꿨으면 그 플랫폼 라이브 확인 결과를 PR에 적는다.
- 한 계정에는 코어 하나(saya 어댑터 id `chatplus`). 같은 계정을 두 PC에서 동시에 답하지 않는다.

## COMMANDS
```bash
uv sync --frozen
uv run basedpyright
uv run ruff check chatplus_core tests tools
uv run ruff format --check chatplus_core tests tools
uv run python -m pytest -q
uv run python -m tools.verify_live     # 실제 캐시, 개수만
uv run python -m tools.verify_socket   # 실제 루프백 서버
CHATPLUS_TOKEN=... CHATPLUS_PORT=8767 uv run python -m chatplus_core
```

## NOTES
- 키는 클라이언트가 그 DB를 연 동안에만 있다. 한 번도 연 적 없는 방은 못 읽는다. `unread`는 늘 0(미매핑).
- mac 보내기는 어댑터를 띄운 터미널에 손쉬운 사용 권한이 필요. 화면 녹화 권한은 필요 없다.
- 채팅+ 자체 잠금 모드 해제(`CHATPLUS_LOCK_PASSWORD`)는 mac 실물로 아직 확인하지 않았다.
- 라이브 보내기 확인은 본인 번호 방에만, 텍스트·이미지 각 1회, 캐시 되읽기까지.
- 남은 일과 결정 이력: `TODO.md`, `.omo/plans/chatplus-core-followup.md`.
