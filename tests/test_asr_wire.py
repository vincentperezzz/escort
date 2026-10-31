from escort.asr_text import ASR_KEYWORDS, dedupe_adjacent, fuzzy_mentions, normalize_asr
from escort.models import Utterance
from escort.prd import keywords_from_prd
from escort.record_eval import SCRIPTS
from escort.spark.muse import spark_model_name
from escort.spark.rules import RuleSpark
from escort.transcribe.events import end_stream_message, handshake_frame, turns_from_file_payload, turns_from_sse
from escort.transcribe.pace import iter_pcm_chunks, pcm_realtime_seconds


def test_realtime_handshake_and_end_stream():
    frame = handshake_frame(
        "LLM_test",
        model="muse-voice-transcribe-1.0",
        mode="ENDPOINTING",
        language_bias=["Tagalog", "English"],
        keywords=list(ASR_KEYWORDS),
    )
    assert frame["authorization"] == {"accessToken": "Bearer LLM_test"}
    assert frame["audioEncoding"] == "PCM_24KHZ"
    assert frame["mode"] == "ENDPOINTING"
    assert frame["languageBias"] == ["Tagalog", "English"]
    for word in ("PRD", "QR", "KYC", "GCash", "BayaniPay", "API"):
        assert word in frame["keywords"]
    assert end_stream_message() == {"type": "endStream"}
    assert "wss://api.meta.ai/v1/asr/realtime".endswith("/asr/realtime")


def test_pcm_is_paced_at_24khz():
    pcm = b"\x00\x01" * 24000
    assert abs(pcm_realtime_seconds(len(pcm)) - 1) < 1e-9
    chunks = iter_pcm_chunks(pcm, chunk_ms=100)
    assert len(chunks) == 10
    assert abs(sum(pcm_realtime_seconds(len(chunk)) for chunk in chunks) - 1) < 1e-6


def test_batch_transcript_drops_adjacent_repeats():
    turns = turns_from_file_payload(
        {"transcript": "team, team", "audioDurationMs": 400, "turns": []},
        "Them",
    )
    assert turns[0].text == "team"
    sse = turns_from_sse(
        'data: {"type":"speechComplete","turnId":1,"transcript":"team, team","audioProcessedMs":400}\n',
        "Me",
    )
    assert sse[0].text == "team"
    assert sse[0].speaker == "Me"


def test_fuzzy_scope_terms_and_payment_conflict():
    assert fuzzy_mentions("the eerie says no", "PRD")
    assert fuzzy_mentions("check the API section", "PRD")
    assert fuzzy_mentions("yung youth code", "QR")
    assert fuzzy_mentions("kulang yung e-hay", "KYC")
    assert fuzzy_mentions("bayani pi wallet", "BayaniPay")
    assert fuzzy_mentions("gcash is just cash", "GCash")
    assert normalize_asr("team, team") == "team"
    heard = "Pwede ba nating i-pull yung cash checkout into Phase 1?"
    trigger = Utterance(
        id="u",
        speaker="Them",
        text=heard,
        start_ms=0,
        end_ms=10,
        finished_at=0,
        created_at="t",
    )
    prd = (
        "### 2.2 Phase 2 — Payment integration\n"
        "Payment integration ships in Phase 2. It is not part of Phase 1.\n"
    )
    cards = RuleSpark().review(prd_text=prd, utterances=[trigger], trigger=trigger)
    assert any(card.type == "PRD_CONFLICT" and "2.2" in (card.prd_section or "") for card in cards)


def test_keywords_always_include_the_short_list():
    words = keywords_from_prd("hello")
    for word in ASR_KEYWORDS:
        assert word in words


def test_spark_never_uses_the_contributor_model():
    assert spark_model_name("muse-spark-1.3-contributor") == "muse-spark-1.3"
    assert spark_model_name("muse-spark-1.3") == "muse-spark-1.3"


def test_record_scripts_cover_the_misheard_terms():
    blob = " ".join(SCRIPTS).casefold()
    assert len(SCRIPTS) == 3
    for word in ("payment integration", "Phase 1", "yung nasa taas", "GCash", "QR", "PRD", "KYC", "BayaniPay", "API"):
        assert word.casefold() in blob
