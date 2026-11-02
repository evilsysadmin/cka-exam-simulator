import React from 'react'
import ReactDOM from 'react-dom/client'
import { useState } from 'react'
import { PilotLabs } from './PilotLabs'
import { ReleaseUpdate } from './ReleaseUpdate'
import './style.css'
function LocalApp() {
  const [editing,setEditing]=useState(false)
  return <><header className="topbar"><a className="brand" href="/">CKA // LOCAL</a><span>Tu entorno de práctica · sin cuenta online</span></header><main className="main"><ReleaseUpdate editing={editing}/><PilotLabs local onEditingChange={setEditing}/></main><footer className="footer">CKA // LAB · SHADOWOPS · LOCAL</footer></>
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><LocalApp/></React.StrictMode>)
