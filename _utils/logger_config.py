import logging
from pathlib import Path


def setup_logging(filename='app3.log'):
    log_path = Path(filename)
    if not log_path.is_absolute():
        log_path = Path(__file__).resolve().parent.parent / log_path

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    file_handler = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    root_logger.propagate = False


logger = logging.getLogger("MyProject")