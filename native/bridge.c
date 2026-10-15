/* Linux adapter. The upstream format writer is kept in vendor/ps5gc. */
static int linux_level = 1;
#define GC_PFSC_ZLIB_LEVEL linux_level
#include "../vendor/converter/ps5gc/pfs_compress.c"

struct job_state g_job = {.lock = PTHREAD_MUTEX_INITIALIZER};

void job_set_current(const char *s) {
  pthread_mutex_lock(&g_job.lock);
  snprintf(g_job.current, sizeof(g_job.current), "%s", s ? s : "");
  pthread_mutex_unlock(&g_job.lock);
}
void job_set_phase(const char *s, long step, long count, const char *current) {
  pthread_mutex_lock(&g_job.lock);
  snprintf(g_job.phase, sizeof(g_job.phase), "%s", s ? s : "");
  pthread_mutex_unlock(&g_job.lock);
  atomic_store(&g_job.phase_step, step);
  atomic_store(&g_job.phase_count, count);
  if(current) job_set_current(current);
}
void job_set_target(const char *s) { (void)s; }
int job_cancelled(void) { return atomic_load(&g_job.cancel); }
void converter_cancel(void) { atomic_store(&g_job.cancel, 1); }

typedef struct linux_extent {
  uint64_t logical, physical, size;
} linux_extent_t;
typedef struct linux_entry {
  const char *rel, *source;
  uint64_t size;
  int64_t mtime;
  const linux_extent_t *extents;
  size_t count;
} linux_entry_t;

static int linux_segment_cmp(const void *a, const void *b) {
  const pfs_segment_t *x = a, *y = b;
  return (x->offset > y->offset) - (x->offset < y->offset);
}

void converter_progress(uint64_t *done, uint64_t *total, uint64_t *written,
                        char *current, size_t cap) {
  *done = atomic_load(&g_job.copied_bytes);
  *total = atomic_load(&g_job.total_bytes);
  *written = atomic_load(&g_job.compressed_output_bytes);
  pthread_mutex_lock(&g_job.lock);
  snprintf(current, cap, "%s", g_job.current);
  pthread_mutex_unlock(&g_job.lock);
}

/* One conversion per worker process. Data sources are never opened writable. */
int converter_run(const linux_entry_t *entries, size_t count,
                  const char *title, const char *source, const char *output,
                  int workers, int level, char *err, size_t err_size) {
  pfs_compress_plan_t plan = {0};
  scan_list_t scans = {0};
  pfs_layout_t *l = &plan.nested;
  int rc = -1;
  linux_level = level;
  if(level < 1 || level > 9 || workers < 1 || workers > 64 || !count) {
    set_err(err, err_size, "invalid conversion parameters");
    return -1;
  }
  for(size_t i = 0; i < count; i++) {
    char key[64];
    snprintf(key, sizeof(key), "@%zu", i);
    if(strlen(entries[i].rel) >= 1000 || strlen(entries[i].source) >= 1024 ||
       scan_push(&scans, key, entries[i].rel, entries[i].size, entries[i].mtime)) {
      set_err(err, err_size, "unsupported path or out of memory");
      goto done;
    }
  }
  qsort(scans.items, scans.count, sizeof(scans.items[0]), scan_file_cmp);
  scan_move_tail_payload_files_to_tail(&scans);
  job_set_current("Preparando imagem exFAT");
  if(build_exfat_layout_from_scans(&scans, title, l, err, err_size)) goto done;
  /* Replace virtual file segments with their real extents (including fragmented
     exFAT chains). Gaps are implicitly zero-filled by virtual_reader_read. */
  pfs_segment_t *old = l->segments;
  size_t old_count = l->segment_count;
  l->segments = NULL;
  l->segment_count = l->segment_cap = 0;
  for(size_t i = 0; i < old_count; i++) {
    pfs_segment_t *s = &old[i];
    if(s->type == PFS_SEG_MEM) {
      if(layout_add_segment_ex(l, s->offset, s->size, s->type, s->mem,
                               NULL, s->force_raw)) { free(old); goto done; }
      continue;
    }
    size_t index = (size_t)strtoull(s->path + 1, NULL, 10);
    if(index >= count) { free(old); goto done; }
    const linux_entry_t *e = &entries[index];
    for(size_t j = 0; j < e->count; j++) {
      const linux_extent_t *x = &e->extents[j];
      if(x->logical > e->size || x->size > e->size - x->logical ||
         x->physical > INT64_MAX - x->size ||
         layout_add_segment_ex(l, s->offset + x->logical, x->size,
                               PFS_SEG_FILE, NULL, e->source, s->force_raw)) {
        set_err(err, err_size, "invalid source extent");
        free(old); goto done;
      }
      l->segments[l->segment_count - 1].source_offset = x->physical;
    }
  }
  free(old);
  qsort(l->segments, l->segment_count, sizeof(*l->segments), linux_segment_cmp);
  snprintf(plan.info.title_id, sizeof(plan.info.title_id), "%s", title);
  snprintf(plan.info.source_path, sizeof(plan.info.source_path), "%s", source);
  snprintf(plan.info.output_path, sizeof(plan.info.output_path), "%s", output);
  snprintf(plan.info.nested_name, sizeof(plan.info.nested_name), "%s.exfat", title);
  plan.info.source_type = PFS_COMPRESS_SOURCE_APP;
  plan.info.format = PFS_COMPRESS_FORMAT_EXFAT;
  plan.info.nested_type = PFS_NESTED_EXFAT;
  plan.info.delete_policy = PFS_DELETE_KEEP;
  plan.info.nested_size = l->image_size;
  rc = pfs_compress_execute_prepared_to_ffpfsc(&plan, workers, NULL, err, err_size);
done:
  free(scans.items);
  layout_free(l);
  if(rc && !err[0]) set_err(err, err_size, "conversion failed");
  return rc;
}

int converter_index(const linux_entry_t *entries, size_t count,
                    const char *output, char *err, size_t cap) {
  ampr_index_entry_t *rows = calloc(count, sizeof(*rows));
  unsigned char *data = NULL;
  size_t size = 0, n = 0;
  ampr_index_stats_t stats;
  if(!rows) return -1;
  for(size_t i = 0; i < count; i++) {
    if(entries[i].size == UINT64_MAX) continue;
    rows[n++] = (ampr_index_entry_t){entries[i].rel, entries[i].size, entries[i].mtime};
  }
  int rc = ampr_index_build_to_memory(rows, n, 0, &data, &size, &stats, err, cap);
  if(!rc) {
    int fd = open(output, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if(fd < 0) rc = -1;
    else {
      rc = write_all_fd(fd, data, size);
      if(close(fd)) rc = -1;
    }
    if(rc) set_err(err, cap, "write index: %s", strerror(errno));
  }
  free(data); free(rows);
  return rc;
}

int write_all_fd(int fd, const void *data, size_t size) {
  const char *p = data;
  while(size) {
    ssize_t n = write(fd, p, size);
    if(n < 0 && errno == EINTR) continue;
    if(n <= 0) return -1;
    p += n; size -= n;
  }
  return 0;
}
