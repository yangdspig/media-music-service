<template>
  <div class="rounded-md border border-border p-3 space-y-3 text-xs">
    <div class="flex flex-wrap gap-2 items-center"><span class="mm-badge" :class="result.dry_run ? 'badge-running' : result.status === 'success' && !['failed', 'unmatched'].includes(result.action) ? 'badge-success' : 'badge-failed'">{{ result.dry_run ? '预览结果（未写入）' : ['failed', 'unmatched'].includes(result.action) ? '处理未完成' : result.status === 'success' ? '处理完成' : result.status === 'partial' ? '部分成功' : '处理失败' }}</span><span v-if="result.action">{{ labels[result.action] || result.action }}</span><span v-if="result.scanned != null">扫描 {{ result.scanned }} 首</span></div>
    <p v-if="result.has_more" class="text-[var(--state-warning)]">仍有曲目未处理，可按艺人或专辑缩小范围后继续回填。</p>
    <p v-if="result.error" class="text-[var(--state-error)] break-words">{{ result.error }}</p>
    <template v-for="key in ['deleted_files', 'removed_dirs']" :key="key"><div v-if="result[key]?.length"><p class="font-medium mb-1">{{ key === 'deleted_files' ? '删除文件' : '清理目录' }} · {{ result[key].length }}</p><ul class="max-h-48 overflow-y-auto space-y-1"><li v-for="path in result[key]" :key="path" class="mono break-all">{{ path }}</li></ul></div></template>
    <ul v-if="result.migrated?.length" class="max-h-48 overflow-y-auto space-y-2"><li v-for="(item, index) in result.migrated" :key="index" class="break-all">{{ item.from }}<br /><span class="text-primary">→ {{ item.to }}</span></li></ul>
    <p v-if="result.skipped?.length">已跳过 {{ result.skipped.length }} 项（目标已存在）</p>
    <ul v-if="result.tracks?.length" class="max-h-64 overflow-y-auto divide-y divide-border"><li v-for="(item, index) in result.tracks" :key="index" class="py-2"><p class="break-words">{{ item.title || item.path }} · {{ labels[item.status] || item.status }}{{ item.score != null ? ` · 匹配分 ${item.score}` : '' }}</p><p v-if="item.path" class="mono text-muted-foreground break-all mt-1">{{ item.path }}</p><p v-if="item.error" class="text-[var(--state-error)] mt-1">{{ item.error }}</p></li></ul>
    <p v-if="result.old" class="break-all">原文件：{{ result.old.file }}</p><p v-if="result.new" class="break-all">新候选：{{ result.new.title }} · {{ result.new.ext }} · 匹配分 {{ result.new.score }}</p>
    <p v-for="(error, index) in result.errors" :key="index" class="text-[var(--state-error)] break-words">{{ error }}</p>
    <p v-if="result.dry_run && !(result.tracks?.length || result.migrated?.length || result.deleted_files?.length || result.removed_dirs?.length)" class="text-muted-foreground">未找到需要处理的项目。</p>
  </div>
</template>
<script setup>
defineProps({ result: { type: Object, required: true } })
const labels = { replaced: '已替换', kept: '保留现有版本', failed: '失败', unmatched: '未匹配', matched: '已匹配，待写入', written: '已写入歌词', already_has_lyrics: '已有歌词，跳过', error: '写入失败' }
</script>
