# JpgLossless Context

JpgLossless is a local image optimizer that chooses a safe output strategy for each input and reports the resulting artifact to the desktop UI.

## Language

**Source image**:
The user-selected image that must remain untouched unless explicit automatic deletion is enabled after a successful output.

**Output artifact**:
The file produced by an optimization or format conversion. Its path and byte size are the source of truth for result reporting.

**Lossless optimization**:
A transformation that preserves decoded pixels, such as ECT or jpegtran optimization. A smaller result is preferred; an enlarged result is not written over the smaller source.

**Lossy conversion**:
A format conversion where quality and byte size are traded, such as JPG or lossy WebP output. A size cap is a target and must be reported as unmet when the lowest supported quality still exceeds it.

**Size cap**:
The maximum requested output size for lossy conversion. It does not apply to the original-format lossless workflow.

_Avoid_: output limit when referring to the lossless workflow.

**Engine**:
An external optimizer or Pillow encoder selected for a format and operation. ECT is the primary lossless engine; jpegtran is a JPEG fallback.
