import { useState } from "react";
import { PageHeader } from "../components/ui.jsx";
import AddContextMenu from "../components/context/AddContextMenu.jsx";
import ContextFilesList from "../components/context/ContextFilesList.jsx";
import FolderSyncDialog from "../components/context/FolderSyncDialog.jsx";
import GoogleImportDialog from "../components/GoogleImport.jsx";
import NotionImportDialog from "../components/context/NotionImportDialog.jsx";
import PasteNoteDialog from "../components/context/PasteNoteDialog.jsx";
import ProjectMeetingsPanel from "../components/context/ProjectMeetingsPanel.jsx";
import { api, apiUpload } from "../lib/api.js";

export default function ContextScreen({ status, refresh, go }) {
  const ctx = status?.context || {};
  const sections = status?.meeting?.prd_sections || [];
  const activeProj = status?.active_project || {};
  const [filter, setFilter] = useState("all");
  const [drag, setDrag] = useState(false);
  const [dialog, setDialog] = useState(null);

  const prepEventId = ctx.prep?.selected_event_id;
  const items = ctx.items || [];
  const chatCount = (ctx.chat || []).length;

  async function uploadFiles(fileList) {
    for (const file of [...fileList]) await apiUpload("/api/context/upload", file);
    refresh();
  }

  async function removeItem(id) {
    await api(`/api/context/items/${id}`, { method: "DELETE" });
    refresh();
  }

  function openDialog(kind) {
    setDialog(kind);
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Context" lede="Sources Escort checks on calls. Add files, link meetings, and prep the next one." />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_380px]">
        <ContextFilesList
          items={items}
          filter={filter}
          onFilter={setFilter}
          onRemove={removeItem}
          drag={drag}
          onDrag={setDrag}
          onUpload={uploadFiles}
          addControl={<AddContextMenu onPick={openDialog} onUploadFiles={uploadFiles} />}
        />

        <ProjectMeetingsPanel
          projectId={activeProj.id}
          prepEventId={prepEventId}
          initialProposals={ctx.prep?.proposals}
          initialPickedIds={ctx.prep?.confirmed_ids}
          onPrepSaved={refresh}
          onGoChat={() => go("chat")}
          chatCount={chatCount}
          sections={sections}
          go={go}
        />
      </div>

      {dialog === "notion" ? <NotionImportDialog onClose={() => setDialog(null)} onImported={refresh} /> : null}
      {dialog === "google" ? <GoogleImportDialog onClose={() => setDialog(null)} refresh={refresh} /> : null}
      {dialog === "folder" ? (
        <FolderSyncDialog
          projectId={activeProj.id}
          projectName={activeProj.name}
          initialPath={activeProj.folder_path}
          onClose={() => setDialog(null)}
          onSynced={refresh}
        />
      ) : null}
      {dialog === "note" ? <PasteNoteDialog onClose={() => setDialog(null)} onSaved={refresh} /> : null}
    </div>
  );
}
