import { useEffect, useRef, useState } from 'react'
import { Trash2, Upload } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'

interface AvatarCropperDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  employeeId: string
  hasExistingAvatar: boolean
  onSaved: () => Promise<void> | void
}

const CANVAS_SIZE = 240
const EXPORT_SIZE = 320

export function AvatarCropperDialog({
  open,
  onOpenChange,
  employeeId,
  hasExistingAvatar,
  onSaved,
}: AvatarCropperDialogProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [imageEl, setImageEl] = useState<HTMLImageElement | null>(null)
  const [zoom, setZoom] = useState<number>(1)
  const [offsetX, setOffsetX] = useState<number>(0)
  const [offsetY, setOffsetY] = useState<number>(0)
  const [dragging, setDragging] = useState(false)
  const dragStartRef = useRef<{ x: number; y: number; ox: number; oy: number }>({
    x: 0,
    y: 0,
    ox: 0,
    oy: 0,
  })
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setImageEl(null)
      setZoom(1)
      setOffsetX(0)
      setOffsetY(0)
      setErrorMsg(null)
    }
  }, [open])

  const drawPreview = (
    canvas: HTMLCanvasElement | null,
    img: HTMLImageElement | null,
    z: number,
    ox: number,
    oy: number,
  ) => {
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.fillStyle = '#f8fafc'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    if (!img) return

    const baseScale = Math.max(
      canvas.width / img.naturalWidth,
      canvas.height / img.naturalHeight,
    )
    const scale = baseScale * z
    const drawW = img.naturalWidth * scale
    const drawH = img.naturalHeight * scale
    const dx = (canvas.width - drawW) / 2 + ox
    const dy = (canvas.height - drawH) / 2 + oy
    ctx.drawImage(img, dx, dy, drawW, drawH)
  }

  useEffect(() => {
    drawPreview(canvasRef.current, imageEl, zoom, offsetX, offsetY)
  }, [imageEl, zoom, offsetX, offsetY])

  const handleFileChange = (file: File | null) => {
    setErrorMsg(null)
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const img = new Image()
      img.onload = () => {
        setImageEl(img)
        setZoom(1)
        setOffsetX(0)
        setOffsetY(0)
      }
      img.src = String(reader.result)
    }
    reader.readAsDataURL(file)
  }

  const handleUpload = async () => {
    if (!imageEl) return
    setBusy(true)
    setErrorMsg(null)
    try {
      const offscreen = document.createElement('canvas')
      offscreen.width = EXPORT_SIZE
      offscreen.height = EXPORT_SIZE
      const ratio = EXPORT_SIZE / CANVAS_SIZE
      drawPreview(
        offscreen,
        imageEl,
        zoom,
        offsetX * ratio,
        offsetY * ratio,
      )

      const blob = await new Promise<Blob | null>((resolve) =>
        offscreen.toBlob((b) => resolve(b), 'image/jpeg', 0.9),
      )
      if (!blob) {
        setErrorMsg(t('common.error'))
        return
      }
      if (blob.size > 2 * 1024 * 1024) {
        setErrorMsg(t('employees.avatar.tooLarge'))
        return
      }

      const formData = new FormData()
      formData.append('file', blob, 'avatar.jpg')
      const headers = await getAuthHeaders()
      const res = await fetch(
        `${getApiBaseUrl()}/api/v1/employees/${employeeId}/avatar`,
        {
          method: 'POST',
          headers,
          body: formData,
        },
      )
      if (!res.ok) {
        setErrorMsg(await extractFetchError(res, t('errors.GENERIC')))
        return
      }
      await onSaved()
      onOpenChange(false)
    } catch (err) {
      setErrorMsg(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async () => {
    setBusy(true)
    setErrorMsg(null)
    try {
      const headers = await getAuthHeaders()
      const res = await fetch(
        `${getApiBaseUrl()}/api/v1/employees/${employeeId}/avatar`,
        {
          method: 'DELETE',
          headers,
        },
      )
      if (!res.ok) {
        setErrorMsg(await extractFetchError(res, t('errors.GENERIC')))
        return
      }
      await onSaved()
      onOpenChange(false)
    } catch (err) {
      setErrorMsg(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t('employees.avatar.title')}</SheetTitle>
          <SheetDescription>{t('employees.avatar.description')}</SheetDescription>
        </SheetHeader>

        <div className="mt-4 space-y-4 px-4">
          {errorMsg && (
            <div
              role="alert"
              className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
            >
              {errorMsg}
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="avatar-file-input">
              {t('employees.avatar.selectImage')}
            </Label>
            <Input
              id="avatar-file-input"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
            />
          </div>

          <div className="flex flex-col items-center gap-3 rounded-md border border-slate-200 bg-slate-50 p-4">
            <canvas
              ref={canvasRef}
              width={CANVAS_SIZE}
              height={CANVAS_SIZE}
              onPointerDown={(e) => {
                if (!imageEl) return
                setDragging(true)
                dragStartRef.current = {
                  x: e.clientX,
                  y: e.clientY,
                  ox: offsetX,
                  oy: offsetY,
                }
              }}
              onPointerMove={(e) => {
                if (!dragging) return
                setOffsetX(
                  dragStartRef.current.ox + (e.clientX - dragStartRef.current.x),
                )
                setOffsetY(
                  dragStartRef.current.oy + (e.clientY - dragStartRef.current.y),
                )
              }}
              onPointerUp={() => setDragging(false)}
              onPointerLeave={() => setDragging(false)}
              className="h-60 w-60 cursor-grab rounded-full border border-slate-300 bg-white active:cursor-grabbing"
            />
            {imageEl && (
              <div className="w-full space-y-1">
                <Label htmlFor="avatar-zoom" className="text-xs">
                  {t('employees.avatar.zoom')}
                </Label>
                <input
                  id="avatar-zoom"
                  type="range"
                  min={1}
                  max={3}
                  step={0.05}
                  value={zoom}
                  onChange={(e) => setZoom(Number(e.target.value))}
                  className="w-full accent-[#2563EB]"
                />
              </div>
            )}
          </div>

          <SheetFooter className="flex flex-wrap gap-2 px-0">
            {hasExistingAvatar && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => void handleDelete()}
                className="border-rose-200 text-rose-700 hover:bg-rose-50"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t('employees.avatar.remove')}
              </Button>
            )}
            <Button
              type="button"
              disabled={busy || !imageEl}
              onClick={() => void handleUpload()}
              className="bg-slate-900 text-white hover:bg-slate-800"
            >
              <Upload className="h-4 w-4" aria-hidden="true" />
              {busy ? t('common.loading') : t('employees.avatar.upload')}
            </Button>
          </SheetFooter>
        </div>
      </SheetContent>
    </Sheet>
  )
}
