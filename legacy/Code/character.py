'''
这里还要写一下我应该如何进行角色的安排
首先先把角色的所有的性质列出来（按照目前的设定）
其所有的参数:
| 角色名      | ATK | HP | 标签     | 设计定位     |
| -------- | --- | -- | ------ | -------- |
| 狂战士      | 5   | 6  | 无      | 高输出、脆皮核心 |
| 铁甲卫士     | 2   | 10 | 重装     | 高耐久前排    |
| 风行射手     | 3   | 5  | 迅捷     | 先手输出     |
| 暗影刺客     | 3   | 4  | 穿透     | 针对高防目标   |
| 圣疗者      | 2   | 7  | 无（或治疗） | 续航 / 稳定器 |
| 均衡战士（可选） | 3   | 7  | 无      | 中庸测试用    |
tags目前的设定：1重装2迅捷3穿透4治疗
整理写作思路：先把需要的主线写出来，拆分游戏部分大概是
1初始化池子（游戏全局，所以固定）
进入三层的循环过程中，针对游戏的把数进行一定的调整（
2进行角色池的抽取
3进行角色加成（针对于hp，atk和操作策略（后期版本二再添加操作策略的可选，目前固定使用hp低者优先攻击的策略，保留修改接口））
4战斗判定（ABAB）

1月31号重新审视这个项目，我感觉我这样编写仍然有缺陷。模块其实理论上还是不够细碎，不方便后续的继续增加功能和更新。例如后期如果我想继续增加角色或者多玩家交互的时候还是不会很方便。
我在思考，战场概念和角色是否应该重构。
先是战场。作为一个引入角色，走流程的一个process运作。
进行交互式角色自身而不是战玩家能左右的。也就是说，在玩家所谓买定离手完成增益操作之后相当于将自己的兵派进战场不再进行指挥操作了，所以这是一个模拟的过程，没有交互的参与必要。
另外需要考虑一下，玩家类是作为一个交互过程起作用的对象，面对的是玩家交互。战场是面向的判定，角色是放置在战场中进行相互作用的对象。其中玩家类是用来保证抽取池的相同，并且进行选定角色，然后将角色投入战场再进行判定
'''
from random import sample,randint
#Characters用于一些初始的角色的模板，其中各个数值代表的意思分别是atk,hp和标签（1重装2迅捷3穿透4治疗）
"""
在这里先解释一下第一个版本中各个标签的作用：
1 重装  受击时如果原始伤害大于等于4时则减少1伤害
2 迅捷  拥有此标签的队伍先手
3 穿透  攻击对象hp大于等于7时造成伤害加1
4 治疗  血量归0时恢复到4一次
"""
Characters=[
    ['狂战士',4,6,0],
    ['铁甲战士',2,8,1],
    ['风行射手',3,5,2],
    ['暗影刺客',3,6,3],
    ['圣疗者',2,5,4],
    ['均衡战士',3,7,0]
]

class Soldier:
    #这是玩家派出的每一个战士的核心模板
    #其中包括每一个战士的基础信息，判定模板，例如可以先导入一个自我伤害判定函数然后再进行受击，还有一些其他特性，
    def __init__(self,name,hp,atk,tagnum):
        self.name = name  #名字
        self.hp = hp  #现有血量
        self.atk = atk   #攻击伤害
        self.maxhp = hp   #最大血量，当然目前没有扣除当然直接导入hp即可
        self.tag = tagnum  #标签的序号
        self.alive=True   #是否存活的状态、
    #受到攻击的时候调用，作用于对象自身，参数分别为攻击伤害和是否具有穿透标签
    def Hurt(self,gotatk,passtag=False):
        if self.tag==1 and gotatk>=4:
            #重装标签受到大于等于4伤害减少伤害1
            gotatk-=1
        elif self.hp>=5 and passtag==True:
            #受到带有穿透标签的攻击并且血量大于7的时候伤害加一
            gotatk+=2
        if self.hp>gotatk:
            #如果一击没死
            self.hp-=gotatk
        elif self.hp<=gotatk:
            #可能死了
            if self.tag==4:
                #如果有治疗，治疗标签消失，血量回3
                self.hp=3
                self.tag=0
                return (self.name+"治愈了自己")
            else:
                #毙命判定
                self.hp=0
                self.alive=False
                return (self.name+"死亡")
        return ""
        #受到攻击之后会返回受攻击报文
    #被call到发出攻击的时候使用，用于判定是否有攻击的资格并且输出伤害和穿透标签，返回伤害和穿透标签
    def Attack(self):
        pstag=False
        #预设没有穿透标签
        if self.alive==False:
            return 0,pstag #已经死亡，类似于打出空击
        #存活
        if self.tag==3:
            #有穿透标签则赋予
            pstag=True
        #返回攻击参数：伤害，穿透标签判定
        return self.atk,pstag

class Battlefield:
    def __init__(self,playern=2,Ctnum=3):
        #只是传入一个玩家数量
        self.num=playern
        self.ctnum=Ctnum
        self.field = []
        self.FirstTeam=-1
        self.TotalHP=[0,0]
    def FindHPMin(self,teamn):
        #这里teamn是从0开始的
        minhp = (100)
        j= -1
        le=len(self.field[teamn])
        for i in range(le):
            if minhp>self.field[teamn][i].hp and self.field[teamn][i].alive==True:
                minhp=self.field[teamn][i].hp
                j=i
        return j
    def SoldierInit(self,PlayerSoldierList):
        #这个playersoldierlist传入的时候是一个列表，里面包含的是几个列表，分别是玩家传入的
        for i in range(self.num):
            PLLS=[]
            for j in PlayerSoldierList[i]:
                #这个j理论上就是每一个战士的元组，应当创建Soldier然后传入战场
                Sd=Soldier(j[0],j[2],j[1],j[3])
                self.TotalHP[i]+=j[2]
                #这里下面一段是设置先手队伍
                if j[3]==2 and self.FirstTeam==-1:
                    self.FirstTeam=i
                elif j[3]==2 and self.FirstTeam!=-1:
                    self.FirstTeam=-1
                #这样子，战场里面分别是代表每一个玩家的列表，内部按顺序包含的是每一个角色的
                PLLS.append(Sd)
            self.field.append(PLLS)
            #这里补充一下，我原来的想法是维护一个堆来使得能快速得出血量最低的角色，但是后面考虑到其实如果不使用额外空间而是每次都进行O(n)的比大小也未必不是个好主意，所以最后选择了擂台得出的解法，但是这里维护一个比大小使用的函数方便后期的改变形式
        if self.FirstTeam==-1:
            if self.TotalHP[0]>self.TotalHP[1]:
                self.FirstTeam=1
            elif self.TotalHP[0]<self.TotalHP[1]:
                self.FirstTeam=0
            else:
                self.FirstTeam=randint(0,1)
            print("没有根据迅捷标签决定先手权，根据机制生成先手为{}".format(self.FirstTeam+1))
        else:
            print("{}队拥有迅捷，先发起了攻击".format(self.FirstTeam+1))


    def RevRange(self):
        if self.FirstTeam==0:
            yield 0
            yield 1
        else:
            yield 1
            yield 0
    def GetTargetGroup(self,teamn):
        if self.num==2:
            return 1-teamn
        #这里就是判断到底对方队伍是哪一只并且return而已
    def StartBattle(self):
        WinFlag=True
        WinTeam=0
        cnt=0
        #标记回合并且设置停止flag，进入while循环
        while WinFlag:
            cnt+=1
            print("回合",cnt)
            #统计回合每次加一
            for i in range(self.ctnum):
                if WinFlag==False:
                    break
                for j in self.RevRange():
                    #现在开始着手修改这里，判定是哪一组人先动的手
                    if self.field[j][i].alive==False:
                        print("{}的{}已经死亡，没有攻击".format(j,self.field[j][i].name))
                        #判断角色是否死亡，不拥有攻击能力
                        continue
                    Tatk,PassTag=self.field[j][i].Attack()
                    #获取攻击信息
                    Tteam=self.GetTargetGroup(j)
                    #获取对方队伍
                    Target=self.FindHPMin(Tteam)
                    #查找对方受攻击对象
                    if Target==-1:
                        #如果没有找到，说明对面队伍死翘了
                        WinFlag=False
                        WinTeam=j
                        break
                    information=self.field[Tteam][Target].Hurt(Tatk,PassTag)
                    #这里设置了Hurt之后会有返回信息说明受到攻击的判定状态
                    print("{}的{}向{}的{}发起了攻击，atk={} {}".format(j+1,self.field[j][i].name,Tteam+1,self.field[Tteam][Target].name,Tatk,information))
        return WinTeam
class Player:
    def __init__(self):
        self.ctnum=3
        self.ctpool=sample(Characters,5)
        #预设摇5个选三个
    def MakeCharacters(self):
        print(self.ctpool)
        chosen=[]
        #这里是不完善的导入机制
        while 1:
            cc=input("在池中选取角色，按照出击顺序排列，空格分割").split(" ")
            if len(list(set(cc)))!=len(cc):
                print("不能重复选择同一个角色，请重新选择")
            else:break
        for i in cc:
            chosen.append(self.ctpool[int(i) - 1].copy())
        print(chosen)
        mc=input("输入增益对象（出击序号和增益项目之间使用空格分割，角色之间使用逗号分割）1代表atk+1,2代表hp+1").split(",")
        for i in mc:
            pl=i.split(" ")
            if pl[1]=="1":
                chosen[int(pl[0])-1][1]+=1
            elif pl[1]=="2":
                chosen[int(pl[0])-1][2]+=1
            #好了，到这里就是处理完成要导入的角色，可以返回了。
        print(chosen)
        return chosen




