import { useRef, useState } from 'react'
import { cn } from './utils'

export function EditableTitle({
  value,
  onSave,
  className,
  placeholderText = 'Click to add title...',
}: {
  value: string | null
  onSave: (next: string | null) => Promise<void>
  className?: string
  placeholderText?: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEditing() {
    setDraft(value ?? '')
    setError(null)
    setEditing(true)
    queueMicrotask(() => inputRef.current?.select())
  }

  async function commit() {
    const next = draft.trim()
    const normalized = next === '' ? null : next
    if (normalized === (value ?? null)) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(normalized)
      setEditing(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div
        className="flex flex-col gap-1"
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
        }}
      >
        <input
          ref={inputRef}
          type="text"
          value={draft}
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setEditing(false)
            }
            e.stopPropagation()
          }}
          placeholder={placeholderText}
          className={cn(
            'w-full rounded-md border border-input bg-background px-2 py-1 font-semibold focus:outline-none focus:ring-2 focus:ring-ring',
            className,
          )}
        />
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    )
  }

  const hasTitle = value != null && value !== ''
  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        startEditing()
      }}
      className={cn(
        'inline-flex items-center text-left rounded-md hover:bg-accent hover:text-accent-foreground focus:outline-none focus:ring-2 focus:ring-ring',
        className,
      )}
      title="Click to edit"
    >
      {hasTitle ? (
        <span>{value}</span>
      ) : (
        <span className="italic text-muted-foreground font-normal">{placeholderText}</span>
      )}
    </button>
  )
}
