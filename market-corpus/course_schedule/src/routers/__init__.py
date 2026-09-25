"""课程表插件路由集合。"""

from .academic import AcademicRouter
from .import_export import ClearDataRouter, ImportExportRouter
from .manage import ManageRouter
from .query import QueryRouter
from .tasks import TasksRouter

__all__ = ["ManageRouter", "QueryRouter", "TasksRouter", "ImportExportRouter", "AcademicRouter", "ClearDataRouter"]
