"""SQLAlchemy 模型包。

在这里 import 所有模型：Base.metadata 只有"见过"这些类，create_all 才会建表。
新增模型后记得补一行，否则表不会被创建。
"""
from models.photo import Photo
from models.price import Price
from models.profile import UserProfile
from models.resort import Resort, Run
from models.user import User

__all__ = ['User', 'UserProfile', 'Resort', 'Run', 'Price', 'Photo']
