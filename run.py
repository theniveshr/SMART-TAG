"""
run.py - Start the SmartTag Toll Gate System
Usage: python run.py
"""
import subprocess, sys, os

def main():
    port = int(os.environ.get("PORT", 8000))
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--reload", f"--port={port}", "--host=0.0.0.0"]
    print(f"Starting SmartTag on http://localhost:{port}")
    subprocess.run(cmd, cwd=os.path.join(os.path.dirname(__file__), "backend"))

if __name__ == "__main__":
    main()
