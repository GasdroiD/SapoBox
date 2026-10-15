/* No embedded APR binary. The operator supplies a compatible PackedStdio SPRX. */
#include "../vendor/converter/lz4gc/ampr_pack.h"
const unsigned char *ampr_pack_runtime_data(size_t *size) {
  if(size) *size = 0;
  return 0;
}
const char *ampr_pack_runtime_sha256(void) { return ""; }
const char *ampr_pack_runtime_version(void) { return "externally supplied PackedStdio"; }
