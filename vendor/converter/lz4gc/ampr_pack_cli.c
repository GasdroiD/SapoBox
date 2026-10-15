/*
 * Host-side driver for the AMPR LZ4 packer. Builds with the system compiler
 * (make host-test) so packs can be produced on a PC and checked with
 * drakmor's ampr_pack.py verify/inspect.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>

#include "ampr_pack.h"

static double
elapsed(void) {
  static double start;
  struct timeval tv;
  gettimeofday(&tv, NULL);
  double now = tv.tv_sec + tv.tv_usec / 1e6;
  if(start == 0) start = now;
  return now - start;
}

static void
on_phase(void *ctx, const char *phase, const char *label) {
  (void)ctx;
  fprintf(stderr, "[phase %7.2fs] %s: %s\n", elapsed(), phase, label);
}

static void
on_progress(void *ctx, uint64_t done, uint64_t total) {
  (void)ctx;
  fprintf(stderr, "\r[progress] %llu / %llu", (unsigned long long)done,
          (unsigned long long)total);
  if(done == total) fputc('\n', stderr);
}

static void
on_log(void *ctx, const char *msg) {
  (void)ctx;
  fprintf(stderr, "[log] %s\n", msg);
}

static unsigned char *
read_all(const char *path, size_t *size) {
  FILE *f = fopen(path, "rb");
  if(!f) return NULL;
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  unsigned char *buf = malloc((size_t)n);
  if(buf && fread(buf, 1, (size_t)n, f) != (size_t)n) {
    free(buf);
    buf = NULL;
  }
  fclose(f);
  *size = (size_t)n;
  return buf;
}

static void
print_stats(const ampr_pack_stats_t *st) {
  printf("{\"files\":%llu,\"packed\":%llu,\"loose\":%llu,\"autoLoose\":%llu,"
         "\"chunks\":%llu,\"lz4\":%llu,\"raw\":%llu,\"shared\":%llu,"
         "\"logical\":%llu,\"stored\":%llu,\"packBytes\":%llu,"
         "\"looseBytes\":%llu,\"manifest\":%llu,\"removed\":%llu,\"packs\":%u,"
         "\"runtime\":%d,\"decoded\":%llu,\"physical\":%llu,\"rtWorkers\":%u,"
         "\"profile\":\"%s\",\"learned\":%llu,\"buildId\":\"%s\"}\n",
         (unsigned long long)st->files_total, (unsigned long long)st->files_packed,
         (unsigned long long)st->files_loose,
         (unsigned long long)st->files_auto_loose,
         (unsigned long long)st->chunks, (unsigned long long)st->chunks_lz4,
         (unsigned long long)st->chunks_raw, (unsigned long long)st->chunks_shared,
         (unsigned long long)st->logical_bytes,
         (unsigned long long)st->stored_bytes, (unsigned long long)st->pack_bytes,
         (unsigned long long)st->loose_bytes,
         (unsigned long long)st->manifest_bytes,
         (unsigned long long)st->removed_bytes, st->pack_count,
         st->runtime_installed,
         (unsigned long long)st->rt_decoded_cache_bytes,
         (unsigned long long)st->rt_physical_cache_bytes, st->rt_workers,
         st->profile, (unsigned long long)st->learned_patterns, st->build_id);
}

int
main(int argc, char **argv) {
  ampr_pack_callbacks_t cb = { NULL, on_phase, on_progress, NULL, NULL, on_log };
  ampr_pack_stats_t st;
  char err[512] = {0};
  if(argc < 3) {
    fprintf(stderr,
            "usage: %s pack <src> <out|--in-place> [--level N] [--workers N]\n"
            "          [--block-kib N] [--profile toml] [--learn dir]\n"
            "          [--runtime sprx] [--embedded-runtime] [--install-runtime-if-missing]\n"
            "          [--no-auto-loose] [--verify-loose]\n"
            "       %s verify <packed> [source]\n"
            "       %s unpack <packed> <out|--in-place>\n"
            "       %s probe <root>\n", argv[0], argv[0], argv[0], argv[0]);
    return 2;
  }
  if(!strcmp(argv[1], "pack") && argc >= 4) {
    ampr_pack_options_t o;
    memset(&o, 0, sizeof(o));
    o.source_root = argv[2];
    if(!strcmp(argv[3], "--in-place")) {
      o.mode = AMPR_PACK_MODE_IN_PLACE;
    } else {
      o.output_root = argv[3];
      mkdir(argv[3], 0777);
    }
    o.level = 9;
    o.auto_loose_large = 1;
    o.cb = cb;
    for(int i = 4; i < argc; i++) {
      if(!strcmp(argv[i], "--level") && i + 1 < argc) o.level = atoi(argv[++i]);
      else if(!strcmp(argv[i], "--workers") && i + 1 < argc) o.workers = atoi(argv[++i]);
      else if(!strcmp(argv[i], "--block-kib") && i + 1 < argc) {
        int kib = atoi(argv[++i]);
        int shift = 10;
        while((1 << shift) < kib * 1024) shift++;
        o.block_shift = shift;
      } else if(!strcmp(argv[i], "--profile") && i + 1 < argc) o.profile_path = argv[++i];
      else if(!strcmp(argv[i], "--learn") && i + 1 < argc) o.learn_dir = argv[++i];
      else if(!strcmp(argv[i], "--runtime") && i + 1 < argc) {
        o.runtime = read_all(argv[++i], &o.runtime_size);
      } else if(!strcmp(argv[i], "--embedded-runtime")) {
        o.runtime = ampr_pack_runtime_data(&o.runtime_size);
      } else if(!strcmp(argv[i], "--install-runtime-if-missing")) o.install_runtime_if_missing = 1;
      else if(!strcmp(argv[i], "--no-auto-loose")) o.auto_loose_large = 0;
      else if(!strcmp(argv[i], "--verify-loose")) o.verify_loose_copies = 1;
      else {
        fprintf(stderr, "unknown option %s\n", argv[i]);
        return 2;
      }
    }
    elapsed();
    if(ampr_pack_build(&o, &st, err, sizeof(err)) != 0) {
      fprintf(stderr, "pack failed: %s\n", err);
      return 1;
    }
    fprintf(stderr, "[done %7.2fs]\n", elapsed());
    print_stats(&st);
    return 0;
  }
  if(!strcmp(argv[1], "verify")) {
    if(ampr_pack_verify(argv[2], argc > 3 ? argv[3] : NULL, &cb, &st, err,
                        sizeof(err)) != 0) {
      fprintf(stderr, "verify failed: %s\n", err);
      return 1;
    }
    print_stats(&st);
    return 0;
  }
  if(!strcmp(argv[1], "unpack") && argc >= 4) {
    const char *out = strcmp(argv[3], "--in-place") ? argv[3] : NULL;
    if(out) mkdir(out, 0777);
    if(ampr_pack_unpack(argv[2], out, &cb, &st, err, sizeof(err)) != 0) {
      fprintf(stderr, "unpack failed: %s\n", err);
      return 1;
    }
    print_stats(&st);
    return 0;
  }
  if(!strcmp(argv[1], "probe")) {
    int ok = ampr_pack_probe(argv[2], &st);
    printf("packed=%d ", ok);
    print_stats(&st);
    return ok ? 0 : 1;
  }
  fprintf(stderr, "bad command\n");
  return 2;
}
