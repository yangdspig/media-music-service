"""pytest 全局 fixture：归档路径的艺人流派兜底（iTunes 联网查询）默认 stub 掉。"""
import pytest


@pytest.fixture(autouse=True)
def _no_local_api_key(monkeypatch):
    """默认隔离本地鉴权配置；鉴权用例在测试内显式设置 API Key。"""
    monkeypatch.setattr("app.config.settings.api_key", None)


@pytest.fixture(autouse=True)
def _no_artist_genre_network(monkeypatch):
    """get_artist_genre 是归档时的联网兜底：测试默认视为查不到（返回 None），
    保证全量测试离线可跑。验证兜底行为的用例在测试内自行 monkeypatch 覆盖。"""
    monkeypatch.setattr("app.itunes.get_artist_genre", lambda artist: None)
