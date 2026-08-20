"use strict";

function closeModal(result = false) {
    const layer = $("#modal-layer");
    layer.hidden = true;
    document.body.classList.remove("modal-open");
    const action = state.modalAction;
    state.modalAction = null;
    state.modalCloseOnBackdrop = true;
    const modalStatus = $("#modal-status");
    if (modalStatus) {
        modalStatus.hidden = true;
        modalStatus.textContent = "";
    }
    if (action) action(result);
}

function showModal({kicker, title, body, confirmText, cancelText, destructive = false, closeOnBackdrop = true}) {
    if (state.modalAction) closeModal(false);
    $("#modal-kicker").textContent = kicker;
    $("#modal-title").textContent = title;
    const bodyElement = $("#modal-body");
    bodyElement.replaceChildren();
    if (body instanceof Node) bodyElement.append(body);
    else bodyElement.textContent = String(body ?? "");
    const confirm = $("#modal-confirm");
    confirm.textContent = confirmText || tr("confirm");
    confirm.className = destructive ? "danger-button" : "primary-button";
    const cancel = $("#modal-cancel");
    cancel.textContent = cancelText ?? tr("cancel");
    cancel.hidden = cancelText === null;
    $("#modal-layer").hidden = false;
    state.modalCloseOnBackdrop = closeOnBackdrop;
    document.body.classList.add("modal-open");
    window.setTimeout(() => confirm.focus(), 0);
    return new Promise((resolve) => {
        state.modalAction = resolve;
    });
}

function showMessage(message, title = null, onClose = null) {
    showModal({
        kicker: tr("notice"),
        title: title || tr("operationFailed"),
        body: message,
        confirmText: tr("close"),
    }).then(() => onClose?.());
    $("#modal-cancel").hidden = true;
}
