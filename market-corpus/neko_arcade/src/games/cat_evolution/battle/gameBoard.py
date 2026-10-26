from typing import TYPE_CHECKING

from .logger import Logger
from .msgManager import MsgManager
from .msgPack import MsgPack

if TYPE_CHECKING:
    from .pokemon import Pokemon


class GameBoard:
    def __init__(self, logger: Logger, msg_manager: MsgManager):
        self.enemy:"Pokemon" = None
        self.our:"Pokemon" = None
        self.msg_manager = msg_manager
        self.logger = logger
        self.TURN_LIMIT = 20

    def add_ally(self, pokemon: "Pokemon"):
        self.our = pokemon

    def add_enemy(self, pokemon: "Pokemon"):
        self.enemy = pokemon

    def init(self):
        # 这个极端特殊的技能，暂且放在外面
        if "镜中自我" in self.enemy.skillGroup:
            self.enemy.ATK = self.our.ATK
            self.enemy.DEF = self.our.DEF
            self.enemy.MAX_HP = self.our.MAX_HP
            self.enemy.SPD = self.our.SPD + 1
            self.enemy.baselv = self.our.baselv
            self.enemy.fakebaselv = self.our.fakebaselv
            self.enemy.lv = self.our.lv
            self.enemy.skillGroup = self.our.skillGroup

        self.our.init()
        self.our.party = "our"

        self.enemy.init()
        self.enemy.party = "enemy"


    def get_enemy(self) -> "Pokemon":
        return self.enemy

    def battle(self):
        our: "Pokemon" = self.our
        enemy: "Pokemon" = self.enemy
        cnt = 1
        self.logger.log(
            f"{our.name}(lv.{our.get_lv()} 攻:{our.get_atk()} 防:{our.get_def()} 生命:{our.get_max_hp()} 速度:{our.get_spd()})")
        self.logger.log(
            f"{enemy.name}(lv.{enemy.get_lv()} 攻:{enemy.get_atk()} 防:{enemy.get_def()} 生命:{enemy.get_max_hp()} 速度:{enemy.get_spd()})")
        self.logger.log("战斗开始")

        while True:
            self.logger.log(f"🔚🔴第{cnt}回合")
            self.msg_manager.send_msg(MsgPack.turn_start_pack(cnt))
            # 第一步比较速度
            if our.get_spd() >= enemy.get_spd():
                our.attack(enemy)
                result = self.death_check(our, enemy)
                if result:
                    return result
                enemy.attack(our)
                result = self.death_check(our, enemy)
                if result:
                    return result
            else:
                enemy.attack(our)
                result = self.death_check(our, enemy)
                if result:
                    return result
                our.attack(enemy)
                result = self.death_check(our, enemy)
                if result:
                    return result
            self.msg_manager.send_msg(MsgPack.turn_end_pack())
            self.msg_manager.turn_end()
            result = self.death_check(our, enemy)
            if result:
                return result
            cnt += 1
            if cnt > self.TURN_LIMIT:
                self.logger.log(f"{self.our.name}和{self.enemy.name}永远战斗在了一起...")
                return "敌人胜利"

    def death_check(self, our: "Pokemon", enemy: "Pokemon") -> str:
        # 死亡结算
        if our.hp <= 0:
            self.logger.log(f"{self.our.name}倒下了...")
            return "敌人胜利"
        if enemy.hp <= 0:
            self.logger.log(f"{self.our.name}一口将{self.enemy.name}吞掉！")
            return "我方胜利"
        return None

    def print_log(self):
        self.logger.print_log()
