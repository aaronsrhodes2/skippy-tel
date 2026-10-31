"""The news on request: "what's the news?" -> "about what?" -> "politics" -> a search -> a conversation; a follow-up searches that one story again."""
import news
from brain import Brain
from timers import Timers


def results(*titles):
    return {"results": [{"title": t, "content": f"About {t}.", "url": f"https://www.example.com/{i}", "publishedDate": None} for i, t in enumerate(titles)]}


class Get:
    def __init__(self, *pages):
        self.pages, self.urls = list(pages), []

    def __call__(self, url):
        self.urls.append(url)
        return self.pages.pop(0) if self.pages else {"results": []}


class B:
    def __init__(self, **k): self.__dict__.update(k)


class Client:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []
        self.messages = self

    def create(self, **k):
        self.calls.append(k)
        return B(content=[B(type="text", text=self.texts.pop(0) if self.texts else "ok")])


def test_bare_news_request_asks_about_what_without_the_model():
    for heard in ("What's the news?", "news", "Any news today?", "give me the headlines", "what's the latest news"):
        assert news.is_bare_request(heard), heard
    for heard in ("what's the news about the election", "news about space", "I read the news", "turn on the news channel"):
        assert not news.is_bare_request(heard), heard
    client = Client()
    brain = Brain(client, "m", Timers(), persona="p", news=news.News({}, get=Get()))
    assert brain.reply("what's the news?") == news.ASK_WHAT and client.calls == []


def test_the_answer_to_about_what_searches_that_subject_recent_news():
    get = Get(results("Senate passes bill", "Governor signs law", "Election day nears"))
    client = Client("Here is politics, monkey. Which one?")
    brain = Brain(client, "m", Timers(), persona="p", news=news.News({}, get=get))
    brain.reply("what's the news")
    assert brain.reply("Politics.") == "Here is politics, monkey. Which one?"
    assert "categories=news" in get.urls[0] and "time_range=day" in get.urls[0] and "q=Politics" in get.urls[0]
    assert brain.actions[0]["tool"] == "news" and brain.actions[0]["args"] == {"subject": "Politics"}
    sent = client.calls[0]["messages"][-1]["content"]
    assert "Senate passes bill" in sent and "example.com" in sent and "tools" not in client.calls[0]   # the model only words it
    assert "Senate passes bill" in brain.system()                     # remembered for the follow-up


def test_a_quiet_day_widens_to_the_week():
    get = Get(results("Only one"), results("A", "B", "C"))
    out = news.News({}, get=get).run({"subject": "chess"})
    assert "time_range=week" in get.urls[1] and "the last week" in out and "1. A" in out


def test_more_on_the_second_one_searches_that_story_and_keeps_the_list():
    get = Get(results("Senate passes bill", "Governor signs housing law", "Election day nears"), results("Housing law details", "Reactions"))
    client = Client("list", "more")
    brain = Brain(client, "m", Timers(), persona="p", news=news.News({}, get=get))
    brain.reply("what's the news")
    brain.reply("politics")
    brain.reply("tell me more about the second one")
    assert brain.actions[0]["args"] == {"subject": "Governor signs housing law", "story": 2}
    assert "q=Governor+signs+housing+law" in get.urls[1] and "time_range=week" in get.urls[1]
    assert "Senate passes bill" in brain.system() and "Housing law details" in brain.system()


def test_a_story_named_by_its_words():
    n = news.News({}, get=Get(results("Dwarf planet found beyond Pluto", "Meteorite traced")))
    n.run({"subject": "space"})
    assert n.story_of("what about the dwarf planet?") == 1
    assert n.story_of("tell me more about the meteorite") == 2
    assert n.story_of("the last one, more please") == 2
    assert n.story_of("turn on the lights") is None


def test_not_a_subject_goes_to_the_model():
    n = news.News({}, get=Get())
    hist = [{"role": "user", "content": "news"}, {"role": "assistant", "content": news.ASK_WHAT}]
    assert n.pre_route("never mind", hist) is None
    assert n.pre_route("um, about space", hist) == {"subject": "space"}
    assert n.pre_route("space", [{"role": "assistant", "content": "Hello"}]) is None


def test_local_news_uses_his_town_and_the_search_failing_is_said_plainly():
    get = Get(results("A", "B", "C"))
    n = news.News({"HOME_ADDRESS": "1 Main St, Springfield, IL 62701"}, get=get)
    assert n.place == "Springfield, IL"
    n.run({"subject": "news near me"})
    assert "Springfield" in get.urls[0]

    def down(url):
        raise OSError("no route")
    assert "not answering" in news.News({}, get=down).run({"subject": "x"})


def test_home_place_forms():
    assert news.home_place({"HOME_ADDRESS": "1 Main St, Springfield, IL 62701, USA"}) == "Springfield, IL"
    assert news.home_place({"HOME_ADDRESS": ""}) == ""
    assert news.home_place({"SKIPPY_NEWS_PLACE": "Paris, France"}) == "Paris, France"
