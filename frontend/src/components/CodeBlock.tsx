import { useState } from 'react'
import { Button } from '@/components/ui/button'

export function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // ignore
    }
  }
  return (
    <div className="relative">
      <pre className="bg-muted text-sm rounded-md p-3 pr-16 overflow-x-auto font-mono">
        {code}
      </pre>
      <Button
        variant="ghost"
        size="sm"
        onClick={onCopy}
        className="absolute top-1.5 right-1.5"
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}
