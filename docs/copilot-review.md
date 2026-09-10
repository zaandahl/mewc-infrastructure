# Copilot review follow-up (10 September 2026)

All 18 inline comments in the seven coordinated pull requests were accepted and addressed. The table records the changes without marking GitHub review conversations resolved.

| Repository | Comment | Disposition |
| --- | --- | --- |
| infrastructure | [Review comment](https://github.com/zaandahl/mewc-infrastructure/pull/3#discussion_r3976461949) | Validators retain read-only artifacts and use the writable /tmp mount. A captured-container-argument regression checks both validator and ordinary stage settings. |
| flow | [Review comment](https://github.com/zaandahl/mewc-flow/pull/13#discussion_r3976434247) | Recognize supported four-character architecture aliases before the historical three-character fallback; cover ENB and ViT aliases. |
| flow | [Review comment](https://github.com/zaandahl/mewc-flow/pull/13#discussion_r3976434292) | Exercise actual process-environment overrides and confirm unrelated environment keys are ignored. |
| detect | [Review comment](https://github.com/zaandahl/mewc-detect/pull/4#discussion_r3976438592) | Reject boolean MIN_EDGES values before numeric coercion; add regression cases. |
| detect | [Review comment](https://github.com/zaandahl/mewc-detect/pull/4#discussion_r3976438637) | Correct the configuration table so MD_FILE and CHECKPOINT_FILE resolve under OUTPUT_DIR. |
| detect | [Review comment](https://github.com/zaandahl/mewc-detect/pull/4#discussion_r3976438675) | Give malformed integer input the same labelled validation error as other invalid integer values. |
| snip | [Review comment](https://github.com/zaandahl/mewc-snip/pull/2#discussion_r3976433415) | Apply documented filter defaults, require an integer MIN_EDGES, and invalidate prior completion before parsing these options. Cover omitted defaults and malformed/fractional/boolean edge counts. |
| predict | [Review comment](https://github.com/zaandahl/mewc-predict/pull/3#discussion_r3976454751) | Require an exact committed release-parent.json match and the expected zaandahl parent repository before any manual release build. Apply the same guard to all four child stages. The digest remains unset pending verified parent publication; local image IDs are not registry digests. |
| predict | [Review comment](https://github.com/zaandahl/mewc-predict/pull/3#discussion_r3976454825) | Require MEWC_FLOW_BASE in Compose and document its source; check both supplied and missing variable behavior. |
| predict | [Review comment](https://github.com/zaandahl/mewc-predict/pull/3#discussion_r3976454884) | Require an existing crop directory before inventory recovery, including empty legacy CSV recovery; test missing, deleted and file paths. |
| predict | [Review comment](https://github.com/zaandahl/mewc-predict/pull/3#discussion_r3976454943) | Run both predictor tests and the shared flow suite explicitly in predictor CI; pin the newly reviewed flow commit. |
| predict | [Review comment](https://github.com/zaandahl/mewc-predict/pull/3#discussion_r3976455000) | Reject supplied empty/null manifests while preserving explicitly omitted-manifest mode; cover contiguous class maps. |
| exif | [Review comment](https://github.com/zaandahl/mewc-exif/pull/1#discussion_r3976440857) | Use a generic relative-path validation label because the helper validates several fields. |
| exif | [Review comment](https://github.com/zaandahl/mewc-exif/pull/1#discussion_r3976440910) | Remove the redundant classifications serialization; retain the final assignment. |
| exif | [Review comment](https://github.com/zaandahl/mewc-exif/pull/1#discussion_r3976440965) | Close both Pillow images with context managers in the losslessness test. |
| box | [Review comment](https://github.com/zaandahl/mewc-box/pull/1#discussion_r3976438465) | Require MEWC_DETECT_BASE in boxing Compose; repair the equivalent EXIF path too. |
| box | [Review comment](https://github.com/zaandahl/mewc-box/pull/1#discussion_r3976438535) | Reject image destinations that collide with box_report.json before copying or rendering; verify incomplete reporting and unchanged source bytes. |
| box | [Review comment](https://github.com/zaandahl/mewc-box/pull/1#discussion_r3976438581) | Use an explicitly supplied immutable stage image in current usage examples across all five application stages. |

## Verification and scope

The updated infrastructure suite passes **116 tests and 44 subtests**. Companion suites pass **227 tests**: flow 43, detect 34, snip 19, predict 65, EXIF 40 and box 26. Predictor CI explicitly runs its 65 tests together with the 43 flow tests. The infrastructure static-check scope also passes.

For each of the four child release workflows, the actual embedded guard was executed against an unset lock, matching digest, mismatched digest and foreign repository: 16 cases passed. All three Compose configurations passed with a fixed parent and rejected a missing required parent. Shell syntax checks passed. These checks did not build or publish images.

The original GPU acceptance is retained unchanged in [pipeline-validation.json](evidence/pipeline-validation.json) and [pipeline-images.json](evidence/pipeline-images.json). Those exact commits produced the 43-image/45-crop run and the bitwise classifier comparison. The current [pipeline-sources.lock.json](../pipeline-sources.lock.json) now pins the review fixes; it is a newer source graph and has **not** had another complete GPU build/run. These fixes preserve the accepted model dispatch, preprocessing, class ordering and numerical tolerances.

The committed release-parent records intentionally have no registry digest yet. Before a child release, a maintainer must verify publication from the recorded parent source commit and commit its digest. The record is an explicit approval, not a cryptographic build attestation. No release was dispatched.
