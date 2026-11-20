-- A dethroned champion's source stays unpublished while it discloses a current champion
-- (coordinator/similarity.py). The coordinator rewrites these rows with every publication, in the
-- same transaction as the promotion that dethroned it; the API publishes no source listed here.
CREATE TABLE source_embargoes (
	source_hash TEXT NOT NULL,
	protects TEXT NOT NULL,
	containment DOUBLE PRECISION NOT NULL,
	since TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (source_hash),
	FOREIGN KEY(source_hash) REFERENCES sources (sha256),
	FOREIGN KEY(protects) REFERENCES sources (sha256),
	CONSTRAINT ck_embargo_containment CHECK (containment >= 0 AND containment <= 1)
);
