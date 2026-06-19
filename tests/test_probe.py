from ncaster.probe import human_duration, human_size


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "2 KB"
    assert human_size(5 * 1024 * 1024) == "5 MB"


def test_human_duration():
    assert human_duration(None) == "?"
    assert human_duration(65) == "01:05"
    assert human_duration(3725) == "01:02:05"
