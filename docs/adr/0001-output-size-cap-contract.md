# ADR-0001: Report Unmet Size Caps Honestly

## Status

Accepted

## Decision

Lossy JPG and WebP conversions lower quality in bounded steps and report whether the requested size cap was met. PNG conversion first produces lossless PNG; when it exceeds the cap, it may try a JPG fallback. If that fallback still exceeds the cap, the fallback file is removed and the PNG remains the output artifact.

## Context

The previous flow always used a success-looking `≤N KB` status after quality reduction, even when the lowest attempted quality remained above the cap. PNG fallback also left an oversized JPG candidate on disk while reporting the PNG path and the candidate's byte size inconsistently.

## Consequences

The UI result is now consistent with the file on disk and callers can distinguish a target that was met from one that was impossible for the chosen format. A cap is still a target rather than a guarantee because image codecs cannot always encode arbitrary content below a requested size.
