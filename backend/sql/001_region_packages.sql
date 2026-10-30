-- Version 1: immutable package bytes, plus one published pointer per region.
-- DDL is explicitly installed by the management command, never at API startup.
CREATE TABLE IF NOT EXISTS region_versions (
    region_id VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    package_version VARCHAR(255) NOT NULL,
    manifest_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    manifest_bytes LONGBLOB NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (region_id, version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS region_files (
    region_id VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    relative_path VARCHAR(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    file_role VARCHAR(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload LONGBLOB NOT NULL,
    PRIMARY KEY (region_id, version_id, relative_path),
    CONSTRAINT region_files_version_fk FOREIGN KEY (region_id, version_id)
        REFERENCES region_versions (region_id, version_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- The composite FK prevents publishing a version belonging to another region.
-- Versions do not point back to this registry, avoiding a circular FK graph.
CREATE TABLE IF NOT EXISTS regions (
    region_id VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_version_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (region_id),
    CONSTRAINT regions_current_version_fk FOREIGN KEY (region_id, current_version_id)
        REFERENCES region_versions (region_id, version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
