import { useEffect, useRef } from 'react'

export function ConfirmFinish({ cancel, confirm }: { cancel: () => void; confirm: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const cancelButton = useRef<HTMLButtonElement>(null)
  useEffect(() => { dialog.current?.showModal(); cancelButton.current?.focus() }, [])
  return <dialog ref={dialog} className="finish-dialog" aria-labelledby="finish-title" onCancel={event => { event.preventDefault(); cancel() }}>
    <h2 id="finish-title">¿Finalizar este intento?</h2>
    <p>Se eliminará el entorno del ejercicio y se liberará la plaza compartida. Esta acción no se puede deshacer.</p>
    <div className="dialog-actions">
      <button ref={cancelButton} type="button" className="button primary" autoFocus onClick={cancel}>Cancelar</button>
      <button type="button" className="button ghost danger" onClick={confirm}>Sí, finalizar intento</button>
    </div>
  </dialog>
}
