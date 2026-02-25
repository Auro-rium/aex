if __name__ == "__main__":
    raise SystemExit(
        "AEX CLI has been removed. Use the web UI (/dashboard, /admin/console) "
        "or run the API directly with: uvicorn aex.daemon.app:app --host 0.0.0.0 --port 9000"
    )
