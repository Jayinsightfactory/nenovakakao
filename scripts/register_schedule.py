"""
NenovaSync30min — Windows Task Scheduler 30분 주기 등록/관리

Usage:
    python register_schedule.py          # 등록
    python register_schedule.py stop     # 일시정지
    python register_schedule.py start    # 재개
    python register_schedule.py run      # 즉시 실행
    python register_schedule.py delete   # 삭제
    python register_schedule.py status   # 상태 확인
"""

import subprocess
import sys

TASK_NAME = "NenovaSync30min"
BAT_PATH = r"C:\Users\USER\nenova_agent\sync_schedule.bat"


def register():
    """30분 주기 스케줄 등록"""
    # 기존 작업 삭제 (있으면)
    subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
    )

    # 30분 주기로 등록
    # /sc MINUTE /mo 30 = 30분마다
    # /st 00:00 = 자정부터 시작
    result = subprocess.run(
        [
            "schtasks", "/create",
            "/tn", TASK_NAME,
            "/tr", BAT_PATH,
            "/sc", "MINUTE",
            "/mo", "30",
            "/st", "00:00",
            "/f",  # 강제 덮어쓰기
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' registered: every 30 minutes")
        print("Check: schtasks /query /tn NenovaSync30min")
    else:
        print(f"Failed: {result.stderr}")

    # 상태 확인
    show_status()


def stop_schedule():
    """스케줄 일시정지"""
    result = subprocess.run(
        ["schtasks", "/change", "/tn", TASK_NAME, "/disable"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' disabled.")
    else:
        print(f"Failed: {result.stderr}")


def start_schedule():
    """스케줄 재개"""
    result = subprocess.run(
        ["schtasks", "/change", "/tn", TASK_NAME, "/enable"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' enabled.")
    else:
        print(f"Failed: {result.stderr}")


def run_now():
    """즉시 1회 실행"""
    result = subprocess.run(
        ["schtasks", "/run", "/tn", TASK_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' triggered.")
    else:
        print(f"Failed: {result.stderr}")


def delete_schedule():
    """스케줄 삭제"""
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' deleted.")
    else:
        print(f"Failed: {result.stderr}")


def show_status():
    """스케줄 상태 확인"""
    status = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"],
        capture_output=True,
        text=True,
    )
    if status.returncode == 0:
        print(status.stdout)
    else:
        print(f"Task '{TASK_NAME}' not found.")


COMMANDS = {
    "stop": stop_schedule,
    "start": start_schedule,
    "run": run_now,
    "delete": delete_schedule,
    "status": show_status,
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        register()
    else:
        cmd = sys.argv[1].lower()
        fn = COMMANDS.get(cmd)
        if fn:
            fn()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: stop, start, run, delete, status")
            sys.exit(1)
