/** 时长（秒）→ m:ss */
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—'
  const total = Math.round(Number(seconds))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 字节数 → 人类可读 */
export function formatSize(bytes) {
  if (bytes == null || Number.isNaN(Number(bytes))) return '—'
  let value = Number(bytes)
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[i]}`
}

/** 音质描述 → 展示文案与档位（无损=绿 高品质=蓝 标准=黄） */
export function qualityInfo(quality) {
  const q = (quality || '').toLowerCase()
  if (!q) return { label: '未知', tier: 'standard' }
  if (q.includes('lossless') || q.includes('flac') || q.includes('hi-res') || q.includes('hires') || q.includes('无损') || q.includes('master')) {
    return { label: '无损', tier: 'lossless' }
  }
  if (q.includes('320') || q.includes('hq') || q.includes('高品') || q.includes('exhigh')) {
    return { label: '高品质', tier: 'high' }
  }
  return { label: '标准', tier: 'standard' }
}

/** 服务端 source 客户端名 → 中文展示名 */
const SOURCE_NAME_MAP = {
  NeteaseMusicClient: '网易云音乐',
  QQMusicClient: 'QQ音乐',
  KugouMusicClient: '酷狗音乐',
  KuwoMusicClient: '酷我音乐',
  MiguMusicClient: '咪咕音乐',
  QianqianMusicClient: '千千音乐',
  QishuiMusicClient: '汽水音乐',
  FiveSingMusicClient: '5sing',
  '5singMusicClient': '5sing',
  XimalayaMusicClient: '喜马拉雅',
  LizhiMusicClient: '荔枝FM',
  QingtingMusicClient: '蜻蜓FM',
  MissevanMusicClient: '猫耳FM',
  DoubanMusicClient: '豆瓣FM',
  YinyuetaiMusicClient: '音悦台',
  BilibiliMusicClient: 'B站音频',
  QuanminKGeClient: '全民K歌',
  SpotifyMusicClient: 'Spotify',
  AppleMusicClient: 'Apple Music',
  YoutubeMusicClient: 'YouTube Music',
  TidalMusicClient: 'TIDAL',
  TIDALMusicClient: 'TIDAL',
  DeezerMusicClient: 'Deezer',
  AmazonMusicClient: 'Amazon Music',
  PandoraMusicClient: 'Pandora',
  SoundCloudMusicClient: 'SoundCloud',
  QobuzMusicClient: 'Qobuz',
  NapsterMusicClient: 'Napster',
  AudiomackMusicClient: 'Audiomack',
  BandcampMusicClient: 'Bandcamp',
  MituMusicClient: '蜜獾音乐',
  BuguyyMusicClient: '布谷音乐',
  YinyuedaoMusicClient: '音乐岛',
  GequbaoMusicClient: '歌曲宝',
  MusicSearchClient: '音乐搜索器',
  MusicsoSearchClient: '音乐搜索器',
}

export function sourceDisplayName(name) {
  if (!name) return '—'
  if (SOURCE_NAME_MAP[name]) return SOURCE_NAME_MAP[name]
  return name.replace(/MusicClient$/, '').replace(/Client$/, '') || name
}

/** 任务状态 → 中文文案 */
export const TASK_STATUS_TEXT = {
  pending: '等待中',
  running: '下载中',
  success: '已完成',
  failed: '失败',
  canceled: '已取消',
}

/** 任务对象 → 展示用名称（单曲取首个曲目，专辑取保存目录尾段兜底） */
export function taskDisplayName(task) {
  const first = task?.results?.[0]
  if (first?.title) {
    const artists = Array.isArray(first.artists) ? first.artists.join('/') : first.artists
    if (task.total > 1) {
      return artists ? `${artists} - ${first.title} 等 ${task.total} 首` : `${first.title} 等 ${task.total} 首`
    }
    return artists ? `${artists} - ${first.title}` : first.title
  }
  if (task?.save_dir) {
    const parts = String(task.save_dir).replace(/\/+$/, '').split('/')
    return parts[parts.length - 1] || task.save_dir
  }
  return task?.message || '—'
}

/** 历史记录没有 manifest_path，按专辑结果的曲目序号或终态消息识别。 */
export function isAlbumTask(task) {
  return Boolean(task?.manifest_path)
    || Boolean(task?.results?.some((result) => result.disc != null && result.track != null))
    || Boolean(task?.message?.startsWith('专辑《'))
}
