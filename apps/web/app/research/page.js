import sourceStatus from "../../public/data/source-status.json";
import ResearchChat from "./research-chat.js";
import "./research.css";

export const metadata = {
  title: "公開資料研究－GovIntel AI",
  description: "以公開問題研究核准來源，核對官方引用、資料時間與涵蓋缺口。",
};

export default function ResearchPage() {
  return <ResearchChat publicationGeneration={sourceStatus.latest_collection_run?.collection_run_id || ""} />;
}
