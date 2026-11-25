import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import zhCN from '@/i18n/locales/zh-CN.json'
import esES from '@/i18n/locales/es-ES.json'

export const SUPPORTED_LOCALES = ['zh-CN', 'es-ES'] as const
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]

void i18n.use(initReactI18next).init({
  resources: {
    'zh-CN': { translation: zhCN },
    'es-ES': { translation: esES },
  },
  lng: 'zh-CN',
  fallbackLng: 'zh-CN',
  interpolation: {
    escapeValue: false,
  },
})

export default i18n
