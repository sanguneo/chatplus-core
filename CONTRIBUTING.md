# 기여 안내

chatplus-core는 한 코드베이스로 채팅+ Windows와 macOS 클라이언트를 모두 답합니다.
대부분의 코드는 두 플랫폼이 함께 쓰고, 창을 조작하는 부분만 갈라집니다.

| 구분 | 파일 |
| --- | --- |
| 공용 | `hive.py`, `values.py`, `reader.py`, `models.py`, `envelope.py`, `api.py`, `stream.py`, `icon.py`, `upload.py`, `sender.py`, `send.py`, `login.py`, `__main__.py`, `contract/` |
| 플랫폼마다 갈라지는 함수 | `storage.py`의 `windows_profile()` / `mac_profile()` |
| Windows 전용 | `winclient.py` (Win32 창 메시지 + Windows OCR) |
| macOS 전용 | `macclient.py` (손쉬운 사용 + `CGEventPostToPid`) |

## 한 플랫폼에서만 작업할 때

- 공용 파일, 계약(`contract/`), 오류 코드, 진입점을 바꿨으면 다른 플랫폼 확인을 요청합니다.
  이슈 템플릿 `check-windows` / `check-macos`로 남깁니다. **확인 요청은 권고이고 병합을 막는
  조건이 아닙니다.**
- 플랫폼 전용 파일만 바꿨으면 그 플랫폼에서 라이브로 확인한 결과를 PR에 적습니다.
- 정적 검사는 어느 플랫폼에서든 양쪽 코드를 모두 봅니다. basedpyright는 `pythonPlatform =
  "Windows"`로 고정하고, 플랫폼 분기는 `sys.platform`이 아니라 실행 시점 `platform.system()`으로
  합니다(`sys.platform` 분기는 반대쪽 코드를 도달 불가로 보고 오류를 냅니다). macOS 코드는
  `ctypes.CDLL`만 써서 Windows 기준 검사에서도 타입이 맞습니다.
- 테스트는 두 플랫폼에서 모두 돕니다. 플랫폼 전용 모듈도 import는 어디서나 되게 둡니다.

## 확인

```sh
uv run basedpyright
uv run ruff check chatplus_core tests tools
uv run ruff format --check chatplus_core tests tools
uv run python -m pytest -q
uv run python -m tools.verify_live
uv run python -m tools.verify_socket
```

라이브 보내기 확인은 본인 번호 방에만, 텍스트와 이미지 각 1회, 캐시 되읽기까지 합니다.
실데이터(방 이름, 본문, 번호, 키)는 로그·이슈·커밋에 남기지 않습니다.
