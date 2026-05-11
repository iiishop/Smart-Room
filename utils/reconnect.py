def exponential_backoff(attempt: int, base: float = 1.0,
                        max_interval: float = 60.0) -> float:
    """计算指数退避等待时间：min(base * 2^attempt, max_interval)"""
    return min(base * (2 ** attempt), max_interval)
