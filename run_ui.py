import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", "src/ui/streamlit_app.py"]
        ).returncode
    )
