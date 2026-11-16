from src.gateway.transcript_order import next_transcript_sequence, transcript_sequences


def test_sequence_survives_trimming_and_restoring_legacy_records():
    rows = [{"text": "old one"}, {"text": "old two"}]
    rows.append({"text": "new", "sequence": next_transcript_sequence(rows)})
    assert transcript_sequences(rows) == [1, 2, 3]
    trimmed = rows[1:]
    assert transcript_sequences(trimmed) == [2, 3]
    assert next_transcript_sequence(trimmed) == 4
    assert next_transcript_sequence([]) == 1
