from __future__ import annotations

from core.research_manager import get_research_manager


def research_mode(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "").strip().lower()
    rm = get_research_manager()

    if action == "start":
        query = (params.get("query") or "").strip()
        if not query:
            return "Missing research query. Provide a topic to research."
        depth = params.get("depth", "standard")

        progress_cb = None
        if player is not None and hasattr(player, "report_research_progress"):
            progress_cb = player.report_research_progress

        speak_cb = None
        if player is not None and hasattr(player, "speak"):
            speak_cb = player.speak

        try:
            session_id = rm.start(query=query, depth=depth, on_progress=progress_cb, on_speak=speak_cb)
            return f"Research started with session id {session_id}."
        except Exception as exc:
            import traceback
            traceback.print_exc()
            err_msg = str(exc) or "Research start failed."
            if player is not None and hasattr(player, "write_log"):
                player.write_log(f"ERR: {err_msg}")
            if player is not None and hasattr(player, "show_content"):
                player.show_content("Research Error", err_msg)
            return f"Research start failed: {err_msg}"

    if action == "cancel":
        session_id = params.get("session_id")
        result = rm.cancel(session_id=session_id)
        if player is not None:
            if hasattr(player, "write_log"):
                player.write_log(f"SYS: {result}")
            if hasattr(player, "show_content"):
                player.show_content("Research", result)
        return result

    if action == "status":
        session_id = params.get("session_id")
        status = rm.status(session_id=session_id)
        return " | ".join(f"{k}: {v}" for k, v in status.items())

    if action == "history":
        n = params.get("n", 50)
        try:
            n = int(n)
        except Exception:
            n = 50
        entries = rm.history(n)
        if not entries:
            return "No research history available."
        lines = [f"{e['created_at']} {e['id']} [{e['status']}] {e['query']}" for e in entries]
        return "\n".join(lines)

    if action == "summary":
        session_id = params.get("session_id")
        return rm.summary(session_id=session_id)

    if action == "export":
        session_id = params.get("session_id")
        path = params.get("path")
        return rm.export(session_id=session_id, path=path)

    if action == "open_paper":
        session_id = params.get("session_id")
        paper_id = params.get("paper_id")
        return rm.open_paper(session_id=session_id, paper_id=paper_id)

    if action == "open_image":
        session_id = params.get("session_id")
        image_id = params.get("image_id")
        return rm.open_image(session_id=session_id, image_id=image_id)

    if action in ("list_sources", "references", "sources"):
        session_id = params.get("session_id")
        return rm.list_sources(session_id=session_id)

    if action in ("open_sources", "open_all_sources", "open_all"):
        session_id = params.get("session_id")
        max_open = params.get("max_open", 5)
        try:
            max_open = int(max_open)
        except Exception:
            max_open = 5
        return rm.open_sources(session_id=session_id, max_open=max_open)

    if action == "save_session":
        session_id = params.get("session_id")
        return rm.save_session(session_id=session_id)

    if action == "restore_session":
        session_id = params.get("session_id")
        if not session_id:
            return "Missing session_id to restore."
        return rm.restore_session(session_id=session_id)

    if action == "enter_mode":
        return rm.enter_mode()

    if action == "exit_mode":
        return rm.exit_mode()

    return (
        "Unknown research_mode action. Use start | cancel | status | history | summary | export | "
        "open_paper | open_image | list_sources | open_sources | save_session | restore_session | "
        "enter_mode | exit_mode."
    )
