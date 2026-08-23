#!/usr/bin/env python3
"""
Main entry point - starts both daemon and web server.
"""
import sys
import os
import multiprocessing
from src.daemon import TrafficMonitor
from src.web import app
import uvicorn

def run_daemon():
    """Run the traffic monitoring daemon."""
    daemon = TrafficMonitor()
    try:
        daemon.start()
    except KeyboardInterrupt:
        pass

def run_web():
    """Run the web server."""
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")

if __name__ == '__main__':
    # Start daemon in background process
    daemon_process = multiprocessing.Process(target=run_daemon, daemon=True)
    daemon_process.start()
    
    try:
        # Run web server in main process
        run_web()
    except KeyboardInterrupt:
        print("\n监控已停止")
        daemon_process.terminate()
        daemon_process.join()
        sys.exit(0)
