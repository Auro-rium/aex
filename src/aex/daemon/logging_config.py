import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        
        # Merge extra fields if they exist
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields) # type: ignore

        return json.dumps(log_entry)

def setup_logging(level: str = "INFO"):
    logger = logging.getLogger("aex")
    logger.setLevel(level)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    # Remove existing handlers to avoid duplicates
    logger.handlers = []
    logger.addHandler(handler)

    # Add file handler if AEX_LOG_DIR is set
    import os
    log_dir = os.getenv("AEX_LOG_DIR")
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = logging.FileHandler(os.path.join(log_dir, "aex.log"))
            file_handler.setFormatter(JSONFormatter())
            logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(f"Failed to setup file logging: {e}\n")
    
    # Suppress uvicorn access logs to avoid distinct format
    logging.getLogger("uvicorn.access").disabled = True

def get_logger(name: str):
    return logging.getLogger(f"aex.{name}")

class StructuredLogger:
    def __init__(self, name: str):
        self.logger = logging.getLogger(f"aex.{name}")

    def info(self, msg: str, **kwargs):
        self.logger.info(msg, extra={"extra_fields": kwargs})

    def warning(self, msg: str, **kwargs):
        self.logger.warning(msg, extra={"extra_fields": kwargs})

    def error(self, msg: str, **kwargs):
        self.logger.error(msg, extra={"extra_fields": kwargs})

    def critical(self, msg: str, **kwargs):
        self.logger.critical(msg, extra={"extra_fields": kwargs})
