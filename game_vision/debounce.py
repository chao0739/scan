"""多帧去抖（带滞回）。"""
from collections import deque


class Debouncer:
    def __init__(self, window_size=5, enter_min_hits=3, exit_min_misses=4):
        self.window = deque(maxlen=window_size)
        self.enter_min_hits = enter_min_hits
        self.exit_min_misses = exit_min_misses
        self.state = False

    def update(self, raw: bool) -> bool:
        self.window.append(bool(raw))
        hits = sum(self.window)
        misses = len(self.window) - hits
        if not self.state and hits >= self.enter_min_hits:
            self.state = True
        elif self.state and misses >= self.exit_min_misses:
            self.state = False
        return self.state

    def reset(self):
        self.window.clear()
        self.state = False
