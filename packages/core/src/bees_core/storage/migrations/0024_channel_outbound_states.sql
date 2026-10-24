-- Saída de canal registrada antes do envio: queda no meio vira incerta e nunca é reenviada.
CREATE TABLE channel_events_next (
    link_id TEXT NOT NULL REFERENCES channel_links(id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK(direction IN ('in','out')),
    external_id TEXT NOT NULL CHECK(length(external_id) BETWEEN 1 AND 200),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('received','processed','ignored','failed','sending','sent','unknown')),
    message_id TEXT,
    PRIMARY KEY(link_id,direction,external_id)
) STRICT;
INSERT INTO channel_events_next SELECT link_id,direction,external_id,created_at,status,message_id
FROM channel_events;
DROP TABLE channel_events;
ALTER TABLE channel_events_next RENAME TO channel_events;
