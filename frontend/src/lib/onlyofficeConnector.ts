/* eslint-disable @typescript-eslint/no-explicit-any */
// Helpers over the OnlyOffice editor connector (editor.createConnector()). Data is handed
// to callCommand via Asc.scope — the documented, injection-SAFE mechanism. The command
// function is serialized and run inside the editor; closures do NOT survive, and we must
// NEVER build the command from an interpolated string (eval/Function = code injection).
//
// NOTE: createConnector is only available in OnlyOffice Developer Edition. In Community
// the connector is null, so these are no-ops (callers fall back to re-render + reload).

// `Api` and `Asc` are globals inside the editor's callCommand context (not the page).
declare const Api: any;
declare const Asc: any;

// Highlight `text` in the editor by searching + selecting the first match (the editor's
// native selection both highlights and scrolls it into view).
export function highlightInEditor(connector: any, text: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      const w = window as any;
      if (!connector || !w.Asc) {
        resolve();
        return;
      }
      w.Asc.scope = w.Asc.scope || {};
      w.Asc.scope.needle = text;
      connector.callCommand(
        function () {
          var oDocument = Api.GetDocument();
          var aRanges = oDocument.Search(Asc.scope.needle, false);
          if (aRanges && aRanges.length) {
            aRanges[0].Select();
          }
        },
        () => resolve(),
      );
    } catch {
      resolve();
    }
  });
}

// Replace the first occurrence of `oldText` with `newText`. Resolves true if applied.
export function searchAndReplace(
  connector: any,
  oldText: string,
  newText: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      if (!connector) {
        resolve(false);
        return;
      }
      connector.executeMethod(
        "SearchAndReplace",
        [{ searchString: oldText, replaceString: newText, matchCase: true }],
        () => resolve(true),
      );
    } catch {
      resolve(false);
    }
  });
}
