import { useState } from 'react'

import { errorText } from '../errors'

type Props = {
  value: string | number | null | undefined
  /** Save the new value; null clears the field. Called only when the value changed. */
  onSave: (value: string | null) => Promise<void>
  type?: 'text' | 'number'
  options?: readonly string[]
  required?: boolean
  label: string
  multiline?: boolean
  placeholder?: string
}

/** Field that saves itself on blur (no save button; Product_Requirements §6.2). */
export function AutoField({ value, onSave, type = 'text', options, required, label, multiline, placeholder }: Props) {
  const initial = value == null ? '' : String(value)
  const [draft, setDraft] = useState(initial)
  const [synced, setSynced] = useState(initial)
  const [state, setState] = useState<'idle' | 'saving' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  // Server value changed (save elsewhere, refetch): adopt it (React's derived-state pattern).
  if (synced !== initial) {
    setSynced(initial)
    setDraft(initial)
  }

  async function commit(next: string) {
    if (next === initial) return
    setState('saving')
    try {
      await onSave(next.trim() === '' ? null : next)
      setState('idle')
      setError(null)
    } catch (failure) {
      setState('error')
      setError(errorText(failure))
    }
  }

  const common = {
    'aria-label': label,
    'aria-invalid': state === 'error' || (required && draft.trim() === ''),
    className: `auto-field ${state}`,
    title: error ?? undefined,
  }
  if (options) {
    return (
      <select {...common} value={draft} onChange={(e) => { setDraft(e.target.value); void commit(e.target.value) }}>
        <option value="">-</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  if (multiline) {
    return <textarea {...common} placeholder={placeholder} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={() => void commit(draft)} />
  }
  return (
    <input
      {...common}
      placeholder={placeholder}
      type={type}
      step={type === 'number' ? 'any' : undefined}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => void commit(draft)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
      }}
    />
  )
}
