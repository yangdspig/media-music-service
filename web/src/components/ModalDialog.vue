<template>
  <Teleport to="body">
    <div class="modal-overlay" @click.self="close">
      <section ref="panel" class="modal-panel" :style="{ maxWidth: width }" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1">
        <header class="flex items-center justify-between gap-3 p-4 border-b border-border">
          <h2 class="font-semibold">{{ title }}</h2>
          <button class="mm-btn btn-ghost" aria-label="关闭对话框" :disabled="busy" @click="close"><X class="w-4 h-4" /></button>
        </header>
        <div class="p-4 space-y-4"><slot /></div>
        <footer v-if="$slots.footer" class="p-4 border-t border-border flex flex-wrap justify-end gap-2"><slot name="footer" /></footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { X } from 'lucide-vue-next'

const props = defineProps({ title: String, busy: Boolean, width: { type: String, default: '42rem' } })
const emit = defineEmits(['close'])
const panel = ref(null)
const previousFocus = document.activeElement
const previousOverflow = document.body.style.overflow
function close() { if (!props.busy) emit('close') }
function keydown(event) {
  if (event.key === 'Escape') close()
  if (event.key !== 'Tab') return
  const elements = [...panel.value.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length)
  const first = elements[0], last = elements.at(-1)
  if (!first) { event.preventDefault(); panel.value.focus() }
  else if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.value)) { event.preventDefault(); first.focus() }
}
onMounted(() => { document.body.style.overflow = 'hidden'; panel.value.focus(); document.addEventListener('keydown', keydown) })
onBeforeUnmount(() => { document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', keydown); previousFocus?.focus() })
</script>
