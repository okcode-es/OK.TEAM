export interface SpainRegionOption {
  code: string
  nameEs: string
  nameZh: string
}

export const SPAIN_REGIONS: SpainRegionOption[] = [
  { code: 'ES-MD', nameEs: 'Comunidad de Madrid', nameZh: '马德里自治区' },
  { code: 'ES-CT', nameEs: 'Cataluña', nameZh: '加泰罗尼亚' },
  { code: 'ES-AN', nameEs: 'Andalucía', nameZh: '安达卢西亚' },
  { code: 'ES-VC', nameEs: 'Comunitat Valenciana', nameZh: '瓦伦西亚自治区' },
  { code: 'ES-GA', nameEs: 'Galicia', nameZh: '加利西亚' },
  { code: 'ES-PV', nameEs: 'País Vasco', nameZh: '巴斯克' },
  { code: 'ES-CL', nameEs: 'Castilla y León', nameZh: '卡斯蒂利亚-莱昂' },
  { code: 'ES-CM', nameEs: 'Castilla-La Mancha', nameZh: '卡斯蒂利亚-拉曼恰' },
  { code: 'ES-CN', nameEs: 'Canarias', nameZh: '加那利群岛' },
  { code: 'ES-IB', nameEs: 'Illes Balears', nameZh: '巴利阿里群岛' },
  { code: 'ES-AR', nameEs: 'Aragón', nameZh: '阿拉贡' },
  { code: 'ES-AS', nameEs: 'Principado de Asturias', nameZh: '阿斯图里亚斯' },
  { code: 'ES-CB', nameEs: 'Cantabria', nameZh: '坎塔布里亚' },
  { code: 'ES-EX', nameEs: 'Extremadura', nameZh: '埃斯特雷马杜拉' },
  { code: 'ES-MC', nameEs: 'Región de Murcia', nameZh: '穆尔西亚' },
  { code: 'ES-NC', nameEs: 'Comunidad Foral de Navarra', nameZh: '纳瓦拉' },
  { code: 'ES-RI', nameEs: 'La Rioja', nameZh: '拉里奥哈' },
  { code: 'ES-CE', nameEs: 'Ceuta', nameZh: '休达' },
  { code: 'ES-ML', nameEs: 'Melilla', nameZh: '梅利利亚' },
]

export function formatDurationHhMm(totalMinutes: number): string {
  const safe = Math.max(0, Math.floor(totalMinutes))
  const hours = Math.floor(safe / 60)
  const minutes = safe % 60
  return `${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}min`
}

export function formatSignedSaldoMinutes(saldoMinutes: number): string {
  const sign = saldoMinutes >= 0 ? '+' : '-'
  const abs = Math.abs(Math.round(saldoMinutes))
  const hours = Math.floor(abs / 60)
  const minutes = abs % 60
  return `${sign}${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}min`
}
