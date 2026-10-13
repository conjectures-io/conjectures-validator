-- Supporting papers do not change proof bytes, verification, fees or reward commitments.
CREATE TABLE submission_pdfs (
    id uuid PRIMARY KEY,
    submission_id uuid NOT NULL REFERENCES submissions(id),
    filename text NOT NULL,
    sha256 sha256 NOT NULL,
    byte_length integer NOT NULL,
    content bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT submission_pdf_content_once UNIQUE (submission_id, sha256),
    CONSTRAINT submission_pdf_filename_length CHECK (length(filename) BETWEEN 5 AND 200),
    CONSTRAINT submission_pdf_size CHECK (
        byte_length BETWEEN 1 AND 20971520 AND byte_length = octet_length(content)
    )
);
