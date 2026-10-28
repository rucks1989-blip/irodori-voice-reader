import re


def _extend_boundary(text, end):
    while end < len(text) and text[end] in "。！？!?、，,」』】〕）)]":
        end += 1
    return end


def _delimiter_stack(text, boundary):
    pairs = {"「": "」", "『": "』", "（": "）", "(": ")", "【": "】", "[": "]"}
    reverse = {value: key for key, value in pairs.items()}
    stack = []
    for ch in text[:boundary + 1]:
        if ch in pairs:
            stack.append(ch)
        elif ch in reverse and reverse[ch] in stack:
            index = len(stack) - 1 - stack[::-1].index(reverse[ch])
            del stack[index:]
    return stack


def _protected_close(text, boundary, limit):
    pairs = {"「": "」", "『": "』", "（": "）", "(": ")", "【": "】", "[": "]"}
    stack = _delimiter_stack(text, boundary)
    if not stack:
        return None
    close_at = text.find(pairs[stack[-1]], boundary + 1)
    return close_at if 0 <= close_at < limit else None


def natural_cut(text, target, backtrack, preserve_dialogue=True, forward=20, hard_limit=None):
    target = min(len(text), max(1, int(target)))
    hard_limit = target if hard_limit is None else min(len(text), max(target, int(hard_limit)))
    backward = max(0, int(backtrack))
    forward = max(0, int(forward))
    lower_cut = max(1, target - backward)
    upper_cut = min(hard_limit, target + forward)
    candidates = {
        "sentence": [],
        "exclamation": [],
        "comma": [],
        "paragraph": [],
    }
    for index in range(lower_cut - 1, upper_cut):
        for reason, chars in (("sentence", "。"), ("exclamation", "！？!?"), ("comma", "、，,"), ("paragraph", "\n")):
            if text[index] not in chars:
                continue
            cut = _extend_boundary(text, index + 1)
            if preserve_dialogue:
                close_at = _protected_close(text, index, hard_limit)
                if close_at is not None:
                    cut = _extend_boundary(text, close_at + 1)
                elif _delimiter_stack(text, index):
                    # Do not choose a natural boundary inside an unfinished
                    # dialogue when its closing delimiter is within neither
                    # the search window nor the hard character limit.
                    continue
            if cut <= hard_limit:
                candidates[reason].append(cut)
            break
    for reason in ("sentence", "exclamation", "comma", "paragraph"):
        if candidates[reason]:
            return min(candidates[reason], key=lambda cut: (abs(cut - target), cut > target, cut)), reason
    return target, "hard"


def dialogue_blocks(text, enabled=True):
    if not enabled:
        return [(str(text), "end")]
    pattern = re.compile(r"(?<=[」』])([\s\u3000]*)(?=[「『])")
    blocks, position = [], 0
    for match in pattern.finditer(str(text)):
        part = str(text)[position:match.start()].strip()
        if part:
            blocks.append((part, "dialogue"))
        position = match.end()
    tail = str(text)[position:].strip()
    if tail:
        blocks.append((tail, "end"))
    return blocks


def stable_story_blocks(text, target):
    """Group complete Japanese sentences up to a quality target.

    This is a CPU-only planning step. A sentence longer than the target is kept
    intact here and is handled later by the normal hard-limit splitter.
    """
    source = str(text or "").strip()
    target = max(1, int(target or 1))
    if not source:
        return []
    sentences = [part for part in re.findall(r".*?(?:[。！？!?]+[」』】〕）)]*|$)", source, re.S) if part]
    blocks, current = [], ""
    for sentence in sentences:
        candidate = current + sentence
        if current and len(candidate.strip()) > target:
            blocks.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current.strip():
        blocks.append(current.strip())
    return blocks
