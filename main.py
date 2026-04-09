from __future__ import annotations

from ui import App
from controller import FileTreeController


def main():
    app = App()
    controller = FileTreeController(app)
    app.set_controller(controller)
    app.mainloop()


if __name__ == "__main__":
    main()
