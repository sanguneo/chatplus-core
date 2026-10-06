---
name: Windows 확인 요청
about: 공용 코드 변경을 Windows 채팅+에서 확인해 달라는 요청
title: "check(windows): "
labels: "check: windows"
---

## 바뀐 것

<!-- 커밋/PR과 바뀐 공용 파일 -->

## 확인할 것

- [ ] `uv run python -m pytest -q`
- [ ] `uv run python -m tools.verify_live` (개수만 출력)
- [ ] `uv run python -m tools.verify_socket`
- [ ] (보내기 경로를 바꿨으면) 본인 번호 방에 `/send`·`/upload` 각 1회, 캐시 되읽기

## 결과

<!-- 클라이언트 버전, 통과/실패, 실데이터는 적지 않음 -->
