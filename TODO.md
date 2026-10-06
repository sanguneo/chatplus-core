# TODO — chatplus-core를 Windows·macOS 둘 다로

2026-10-06 작성. nichirin(kanao + kamado)처럼 한 저장소가 두 플랫폼의 채팅+를 모두
답하게 만드는 작업 목록입니다. 이 체크아웃은 Windows 체크아웃(`fe38de3`, v0.8.0)을 git
bundle로 옮긴 것이고, origin은 `git@github.com:sanguneo/chatplus-core.git`입니다.

## 지금 확인된 것 (mac, 192.168.0.5)

| | Windows | macOS |
| --- | --- | --- |
| 앱 | MSIX `com.sktelecom.torchlight_82zr7n7shan6r` | `/Applications/ChattingPlus.app`, `com.sktelecom.torchlight` 1.5.4 (127), 팀 `6BMWJ35A44` |
| UI | Flutter | Flutter (`FlutterMacOS.framework`), App Sandbox |
| 데이터 | `%LOCALAPPDATA%\Packages\…\LocalCache\Local\SK Telecom Co., Ltd\ChattingPlus\edb\<profile>\` | `~/Library/Containers/com.sktelecom.torchlight/Data/Documents/edb/<profile>/` |
| 박스 | `folder.hive`, `object.hive`, `contact_list.hive` … | 같은 이름 (`folder.hive`, `object.hive`, `contact_list.hive` 확인) |
| Hive 키 | `.sK2Nwk*.secure` 파일(AES-GCM) ← 자격 증명 관리자 `key_.` | **앱 `shared_preferences` plist**의 `flutter.cipher_key_<profile>_<기기>`(URL-safe Base64 32바이트, 봉인 없음). 키체인 아님 |
| 보내기 | Win32 창 조작 + Windows OCR | AX(손쉬운 사용) + `CGEventPostToPid` 키 입력, OCR 없음 (`macclient.py`) |

mac 도구: python3 3.9.6(시스템), uv·gh·tailscale 없음. 코어는 Python 3.11 이상 필요.

## 0. 갈림길 — 키를 읽을 수 있는가 (먼저, 이게 막히면 나머지 의미 없음)

- [x] mac에 uv·Python 3.11 이상 (`~/.local/bin/uv`, uv가 3.12 사용)
- [x] 키 위치: 키체인에 없음. `shared_preferences` plist의 `flutter.cipher_key_<profile>_<기기>`가 곧 Hive 키
      (URL-safe Base64 32바이트) — 대안(키체인 프롬프트·프로세스 메모리) 불필요
- [x] `hive.decode_frames`·`values.decode_value`가 mac 박스를 **그대로** 읽음(새 typeId 없음)

## 1. 구조 — nichirin과 같은 원칙, 더 작은 모양

nichirin은 두 저장소를 합쳐서 `windows/`·`macos/` 전체 사본 + `kakaocommon/` 공유입니다.
chatplus-core는 한 코드베이스이고 대부분이 플랫폼 무관이라, 사본 두 벌보다 **플랫폼 모듈 분리**를 권합니다.

- 공용(그대로): `hive.py`, `values.py`, `reader.py`, `models.py`, `envelope.py`, `api.py`, `stream.py`,
  `icon.py`, `upload.py`(라우트), `sender.py`(인터페이스), `send.py`, `contract/`
- 플랫폼별로 갈라질 것:
  - [x] `storage.py`: 공용 부분은 그대로, `windows_profile()` / `mac_profile()`로 나누고 `discover()`가 고름.
        mac 쪽은 plist만 읽는 순수 Python이라 별도 모듈로 빼지 않음
  - [x] `winclient.py`는 Windows 전용 유지(mac에서도 import는 됨), mac은 `macclient.py` 따로
  - [x] `login.py`는 공용 그대로(비밀번호 입력은 각 client가 함)
- [x] `__main__.py`가 `platform.system()`으로 고름. 지원 안 되는 플랫폼은 읽기를 `500 INTERNAL`로 거절하고
      보내기 기능은 신고하지 않음
- [ ] ~~basedpyright `pythonPlatform` 고정 해제~~ — **안 함.** basedpyright는 `sys.platform` 분기로 죽은 코드를
      `reportUnreachable`로 잡아서, 플랫폼별 검사를 하려면 억제가 필요해짐. 대신 플랫폼 분기를 실행 시점
      `platform.system()`으로 하고 mac 코드도 `ctypes.CDLL`만 써서, `Windows` 고정 한 번으로 양쪽 코드를 다 검사함
- [x] 테스트: `test_winclient.py`도 mac에서 그대로 돎(`ctypes.WinDLL` 주석을 `CDLL`로) — 건너뛸 테스트 없음

## 2. macOS 읽기 (rooms.list · messages.read · messages.stream)

- [x] `~/Library/Containers/com.sktelecom.torchlight/Data/Documents/edb/<profile>/` 프로필 찾기
      (프로필이 둘 이상이면 Windows와 같은 규칙)
- [x] 키 가져오기 구현, 키를 파일로 내보내지 않음 (`tests/test_storage.py`)
- [ ] 방 ID `SHA-256(epoch ‖ resource URL)` — epoch는 mac도 프로필 폴더 이름. **두 PC의 방 ID가 같은지는
      Windows 쪽 프로필 이름과 비교해야 해서 아직 확인 못 함**
- [x] 라이브 확인: `/rooms` 45, `/messages`(가장 큰 방 `before` 페이지 = 전체 이력), `/stream` `ready`

## 3. macOS 보내기 (messages.send · attachments.send) — 읽기가 된 다음

- [x] 창 조작: kamado처럼 AX(ctypes) — 채팅+ mac은 Flutter 시맨틱 트리를 AX에 내보냄. 누르기는 `AXPress`,
      글자는 `CGEventPostToPid`(Flutter가 AX로 쓴 값을 안 받음). 앱 활성화·포커스 빼앗기 없음
- [x] OCR 불필요: 방 머리글·말풍선은 AX에 없지만, 받는 사람 검색 결과 중 정확히 하나가 번호와 숫자 그대로
      같을 때만 엶 + 캐시 되읽기. 화면 녹화 권한 불필요, 손쉬운 사용 권한만 필요(README)
- [x] 화면 잠금 감지(`CGSessionCopyCurrentDictionary`) → `REAUTH_REQUIRED`
- [ ] 채팅+ 자체 잠금 모드: Windows와 같은 규칙으로 구현했지만 **mac 잠금 화면 실물로는 미확인**
      (잠금 비밀번호가 없어 직접 잠그지 않음)
- [x] 첨부는 이미지만 — 열기 시트(AX)에서 `~/Downloads/chatplus-core-uploads/`의 파일을 고름
- [x] 라이브 확인: 내 번호 방에 `POST /send`·`POST /upload` 각 1회 `200` + 캐시 되읽기, 미전송 그림이 있을 때
      `403 FORBIDDEN`(아무것도 안 보냄). 그 미전송 그림(합성 8×8 PNG)을 치우려고 직접 보낸 1건이 더 있음

## 4. nichirin에서 가져올 운영 방식

- [x] 루트 `CONTRIBUTING.md`: 공용 코드(위 목록, `contract/`, 진입점, 오류 코드)를 건드리면 양쪽 확인 요청
      ("한 플랫폼에서만 작업할 때" 절 포함, 확인 요청은 권고)
- [x] 이슈 템플릿 `.github/ISSUE_TEMPLATE/check-macos.md` / `check-windows.md`
- [x] 라벨 `check: macos`, `check: windows` — `sanguneo/chatplus-core`에 만듦(gh 2.102.0)
- [x] 루트 `AGENTS.md`(지식 베이스) + "한 플랫폼에서만 작업할 때" 절 — 확인 요청은 권고, 한 계정에 코어 하나
- [x] README: macOS 절(실행·권한·스테이징·확인 기록). capabilities는 두 플랫폼이 같음

## 5. saya 연결

- [x] 결정(owner, 2026-10-06): **같은 계정이면 코어 하나만**, saya 어댑터 id는 `chatplus` 하나.
      PC를 바꿀 때는 업스트림만 바꿈. 다른 계정일 때만 별도 id(`chatplus-mac`) (README 'saya 연결')
- [ ] mac에 tailscale serve(또는 OCI 프록시에서 닿는 경로) + 토큰 파일 권한 600
- [x] 같은 어댑터 계약 버전(현재 0.2.7)을 두 플랫폼이 같이 신고 — 같은 코드·같은 `CONTRACT_VERSION`

## 참고

- 접속: `ssh sanguneo@192.168.0.5` (tailscale `mac` 100.94.66.37로 `sanguneo`는 아직 안 해 봄)
- `192.168.0.6`은 known_hosts 호스트 키 불일치 — mac이 아님
