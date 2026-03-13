# RustDesk Auto-Accept

RustDesk 연결 요청이 들어오면 자동으로 "수락" 버튼을 클릭해주는 프로그램입니다.

RustDesk 유료 기능인 자동 수락을 무료로 구현합니다. Linux와 Windows 모두 지원합니다.

## 빠른 시작

### 1. config.json 설정

```json
{
  "mode": "allow_all"
}
```

모든 연결을 자동 수락합니다. 특정 PC만 허용하려면:

```json
{
  "mode": "whitelist",
  "allowed_ids": ["123456789", "987654321"]
}
```

`allowed_ids`에 허용할 RustDesk ID를 넣으세요. (RustDesk 메인 화면 상단의 숫자)

### 2. 실행

```bash
python3 rustdesk_autoclick.py
```

### 3. (선택) 부팅 시 자동 실행

**Linux:**
```bash
pip install python-xlib
sudo apt install xdotool   # Ubuntu/Debian
./install_linux.sh
```

**Windows:**
```
install_windows.bat
```
관리자 권한을 자동으로 요청합니다.

## 제거

**Linux:** `./uninstall_linux.sh`

**Windows:** `uninstall_windows.bat` 실행

## 설정 상세

`config.json` 전체 옵션:

```json
{
  "mode": "whitelist",
  "allowed_ids": ["YOUR_RUSTDESK_ID_HERE"],
  "dialog_size": {
    "width": 300,
    "height": 490,
    "tolerance": 50
  },
  "button_position": {
    "x_ratio": 0.25,
    "y_ratio": 0.95
  },
  "click_delay": 0.5,
  "idle_threshold": 1.0,
  "idle_timeout": 30.0,
  "log_file": "./autoclick.log"
}
```

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `mode` | `whitelist` | `"whitelist"` = ID 기반 허용, `"allow_all"` = 모두 수락 |
| `allowed_ids` | — | 허용할 RustDesk ID 목록 (whitelist 모드 전용) |
| `dialog_size` | 300x490 | 연결 요청 다이얼로그 크기, `tolerance`는 허용 오차 |
| `button_position` | 0.25, 0.95 | 수락 버튼 위치 (창 크기 대비 비율) |
| `click_delay` | 0.5 | 감지 후 클릭 대기 시간 (초) |
| `idle_threshold` | 1.0 | 마우스가 이 시간동안 멈춰야 클릭 (초) |
| `idle_timeout` | 30.0 | 이 시간이 지나면 마우스 상태와 관계없이 클릭 (초) |
| `log_file` | ./autoclick.log | 로그 파일 경로 |

> 대부분의 경우 `mode`와 `allowed_ids`만 설정하면 됩니다. 나머지는 기본값으로 동작합니다.

## 요구사항

- Python 3.8+
- **Linux**: `python-xlib`, `xdotool` (X11 환경)
- **Windows**: 추가 설치 없음

## 동작 원리

1. RustDesk 프로세스의 창을 **프로세스명 + 크기**로 감지
2. OS 이벤트 + 2초 주기 스캔으로 이중 감지
3. 마우스가 움직이지 않을 때 수락 버튼 클릭
4. 클릭 후 창이 닫혔는지 확인, 실패 시 자동 재시도

## 라이선스

MIT
