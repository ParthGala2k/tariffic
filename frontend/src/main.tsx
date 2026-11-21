import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { DeskProvider } from "./lib/desk";
import Shell from "./components/Shell";
import NeedsYou from "./pages/NeedsYou";
import ActivityPage from "./pages/Activity";
import Items from "./pages/Items";
import Item from "./pages/Item";
import Questions from "./pages/Questions";
import Ledger from "./pages/Ledger";
import Simulate from "./pages/Simulate";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <DeskProvider>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<NeedsYou />} />
            <Route path="activity" element={<ActivityPage />} />
            <Route path="items" element={<Items />} />
            <Route path="items/:id" element={<Item />} />
            <Route path="lawyer" element={<Questions />} />
            <Route path="books" element={<Ledger />} />
            <Route path="dev" element={<Simulate />} />
          </Route>
        </Routes>
      </DeskProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
