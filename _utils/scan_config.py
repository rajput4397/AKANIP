SUPPORTED_INTERVALS = {"daily", "weekly", "15min", "monthly"}


def normalize_interval(value):
    aliases = {
        "1d": "daily",
        "day": "daily",
        "1wk": "weekly",
        "1w": "weekly",
        "week": "weekly",
        "15m": "15min",
        "1mo": "monthly",
        "month": "monthly",
    }
    interval = str(value).strip().lower()
    interval = aliases.get(interval, interval)
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(
            f"Unsupported interval '{value}'. Choose daily, weekly, 15min, or monthly."
        )
    return interval


def load_strategies(strategy_configs, interval, logger, scan_name):
    if not isinstance(strategy_configs, list):
        raise ValueError(f"Scan '{scan_name}': strategies must be a list")

    strategies = []
    for strategy_config in strategy_configs:
        if isinstance(strategy_config, str):
            function_name = strategy_config
            parameters = {}
        elif isinstance(strategy_config, dict):
            function_name = strategy_config.get("function")
            parameters = strategy_config.get("parameters", {})
        else:
            raise ValueError(f"Scan '{scan_name}': each strategy must be a name or mapping")

        if not isinstance(function_name, str) or not function_name:
            raise ValueError(f"Scan '{scan_name}': strategy entries need a function name")
        if not isinstance(parameters, dict):
            raise ValueError(f"Scan '{scan_name}': parameters for '{function_name}' must be a mapping")

        from _utils import all_conditions as conditions
        strategy = getattr(conditions, function_name, None)
        if not callable(strategy):
            logger.warning(
                f"Scan '{scan_name}': strategy '{function_name}' not found in _utils.all_conditions.py"
            )
            continue

        parameters = dict(parameters)
        if function_name == "check_above_ema_20":
            parameters.setdefault("interval", interval)
        strategies.append((function_name, strategy, parameters))
        logger.info(f"Loaded strategy for {scan_name}: {function_name}")

    if not strategies:
        raise ValueError(f"Scan '{scan_name}': no valid strategies configured")
    return strategies
