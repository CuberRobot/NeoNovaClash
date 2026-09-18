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

标签种类
1 自爆      一对一攻击，如果攻击对象血量小于等于24那么直接击杀，否则造成16点伤害，无论如何攻击后自身死亡
2 群体治疗  为死亡队友复活并恢复到一半血量两次（自身死亡不可，若已死亡则不能继续治疗）
3 标签剥夺  随机（或定向，待定）除去对方一个有标签的角色的标签
4 群体伤害转移（护盾）  将部分伤害转移50%到自身身上，3次判定
5 重装盔甲   若受到伤害高于8则减免伤害
6 箭矢穿透   如果攻击对象的原始血量低于一定值（判定为脆皮是原始hp小于等于24）箭矢在攻击本对象之后能进一步攻击另外的对象，伤害相对减少
7 狂暴   血量降到14或一下到一定程度的时候伤害*1.4
8 中毒  在多进行了攻击之后在多个回合中持续造成伤害，可叠加但有判定上限（包括攻击回合和下一个回合）
9 群伤  对对方所有存活单位造成同样伤害

这里开始第二版本的角色设计，鉴于第一个版本可玩性不高，而且相对来说调整的局限性比较大，所以决定重新开始设定
| 角色名        | ATK | HP | 标签     | 设计定位     |
1  均衡战士A    8      19     0
2  均衡战士B    6      23     0
3  均衡战士C    4      26     0
4  自爆步兵   标签判定   -1     1
5  诅咒巫师     6      20     3
6  死灵法师复活  4      24     2
7  铁甲卫士     4      30     5
8  护盾部署者    2      33    4
9  风行射手      7     21     6
10 狂战士       8      19     7
11 毒药投手     6      21     8
12 重炮统领     5      25     9

在这里还要提要：我现将这部分全部改成后端类型的，也就是参数传入-return类型的处理函数而将处理部分全部挑出来，为后续进一步的开发做准备
'''

from random import sample,randint
#Characters用于一些初始的角色的模板，其中各个数值代表的意思分别是atk,hp和标签（1重装2迅捷3穿透4治疗）
Characters=[
    ['均衡战士A',8,19,0],
    ['均衡战士B',6,23,0],
    ['均衡战士C',4,26,0],
    ['自爆步兵',16,-1,1],
    ['诅咒巫师',6,20,3],
    ['死灵法师',4,24,2],
    ['铁甲卫士',4,30,5],
    ['护盾部署者',2,33,4],
    ['风行射手',7,21,6],
    ['狂战士',8,19,7],
    ['毒药投手',6,21,8],
    ['重炮统领',5,25,9]
]

class Soldier:
    #这是玩家派出的每一个战士的核心模板
    #其中包括每一个战士的基础信息，判定模板，例如可以先导入一个自我伤害判定函数然后再进行受击，还有一些其他特性，
    def __init__(self,name,hp,atk,tagnum,teami):
        self.name = name  #名字
        self.hp = hp  #现有血量
        self.atk = atk   #攻击伤害
        self.maxhp = hp   #最大血量，当然目前没有扣除当然直接导入hp即可
        self.tag = tagnum  #标签的序号
        self.alive=True   #是否存活的状态、
        self.team=teami
    #受到攻击的时候调用，作用于对象自身，参数分别为攻击伤害和是否具有穿透标签
    def Hurt(self,gotatk,CureTag,passtag=False,bombtag=False):
        if self.tag==5 and gotatk>=8:
            #重装标签受到大于等于8伤害减少伤害1
            gotatk=(gotatk*6)//10
        elif self.hp<=14 and passtag==True:
            #受到带有狂暴标签的时候伤害乘1.4向下取整
            gotatk=(gotatk*14)//10
        if bombtag==True and self.hp<=24:
            gotatk=25
        if self.hp>gotatk:
            #如果一击没死
            self.hp-=gotatk
        elif self.hp<=gotatk:
            #可能死了
            if CureTag[self.team]>0:
                #如果有治疗，治疗标签消失，血量回3
                self.hp=self.maxhp//2
                CureTag[self.team]-=1
                return (self.name+"被治愈")
            else:
                #毙命判定
                self.hp=0
                self.alive=False
                if self.tag==2:
                    CureTag[self.team]=0
                return (self.name+"死亡")
        return ""
        #受到攻击之后会返回受攻击报文
    #被call到发出攻击的时候使用，用于判定是否有攻击的资格并且输出伤害和穿透标签，返回伤害和穿透标签
    def Attack(self):
        pstag=False
        bombtag=False
        #预设没有穿透标签
        if self.alive==False:
            return 0,pstag #已经死亡，类似于打出空击
        #存活
        if self.tag==7:
            #有穿透标签则赋予
            pstag=True
        if self.tag==1:
            bombtag=True
            self.alive=False
        #返回攻击参数：伤害，穿透标签判定
        return self.atk,pstag,bombtag

class Battlefield:
    def __init__(self,playern=2,Ctnum=3):
        #只是传入一个玩家数量
        self.num=playern
        self.ctnum=Ctnum
        self.field = []
        self.FirstTeam=-1
        self.TotalHP=[0,0]
        self.curetag=[0,0]
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
            removetag=False
            removeteam= -1
            for j in PlayerSoldierList[i]:
                #这个j理论上就是每一个战士的元组，应当创建Soldier然后传入战场
                Sd=Soldier(j[0],j[2],j[1],j[3],i)
                self.TotalHP[i]+=j[2]
                if Sd.tag==2:
                    self.curetag[i]=2
                elif Sd.tag==3:
                    removetag=True
                    removeteam=self.GetTargetGroup(i)
                #这样子，战场里面分别是代表每一个玩家的列表，内部按顺序包含的是每一个角色的
                PLLS.append(Sd)
            self.field.append(PLLS)
            #这里补充一下，我原来的想法是维护一个堆来使得能快速得出血量最低的角色，但是后面考虑到其实如果不使用额外空间而是每次都进行O(n)的比大小也未必不是个好主意，所以最后选择了擂台得出的解法，但是这里维护一个比大小使用的函数方便后期的改变形式
        if removetag==True:
            while 1:
                removec=randint(1,self.num)
                if self.field[removeteam][removec].tag!=0:
                    self.field[removeteam][removec].tag=0
                    break

        if self.FirstTeam==-1:
            if self.TotalHP[0]>self.TotalHP[1]:
                self.FirstTeam=1
            elif self.TotalHP[0]<self.TotalHP[1]:
                self.FirstTeam=0
            else:
                self.FirstTeam=randint(0,1)
            return ("没有根据迅捷标签决定先手权，根据机制生成先手为{}".format(self.FirstTeam+1))
        else:
            return("{}队拥有迅捷，先发起了攻击".format(self.FirstTeam+1))


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
        #这里一个新的改造就是使用长文字输出形式直接Strreturn
        StringRet=""
        WinFlag=True
        WinTeam=0
        cnt=0
        #标记回合并且设置停止flag，进入while循环
        while WinFlag:
            cnt+=1
            StringRet=StringRet+("回合"+str(cnt)+"\n")
            #统计回合每次加一
            for i in range(self.ctnum):
                if WinFlag==False:
                    break
                for j in self.RevRange():
                    #现在开始着手修改这里，判定是哪一组人先动的手
                    if self.field[j][i].alive==False:
                        StringRet=StringRet+("{}的{}已经死亡，没有攻击\n".format(j,self.field[j][i].name))
                        #判断角色是否死亡，不拥有攻击能力
                        continue
                    Tatk,PassTag,BomBtag=self.field[j][i].Attack()
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
                    information=self.field[Tteam][Target].Hurt(Tatk,self.curetag,PassTag,BomBtag)
                    #这里设置了Hurt之后会有返回信息说明受到攻击的判定状态
                    StringRet=StringRet+("{}的{}向{}的{}发起了攻击，atk={} {}\n".format(j+1,self.field[j][i].name,Tteam+1,self.field[Tteam][Target].name,Tatk,information))
        return StringRet,WinTeam
class Player:
    def __init__(self):
        self.ctnum=3
        self.ctpool=sample(Characters,6)
        #预设摇5个选三个
    def GetCharacters(self):
        return self.ctpool
        chosen=[]
        #这里是不完善的导入机制，现在直接废弃不使用，直接传入cc，是一个列表形式的，每个元素代表选中的序号（不是下标）
        """
        while 1:
            cc=input("在池中选取角色，按照出击顺序排列，空格分割").split(" ")
            if len(list(set(cc)))!=len(cc):
                print("不能重复选择同一个角色，请重新选择")
            else:break
        """
    def MakeCharacter(self,cc):
        chosen=[]
        for i in cc:
            chosen.append(self.ctpool[int(i) - 1].copy())
        return chosen
        """
        mc=input("输入增益对象（出击序号和增益项目之间使用空格分割，角色之间使用逗号分割）1代表atk+1,2代表hp+1").split(",")
        for i in mc:
            pl=i.split(" ")
            if pl[1]=="1":
                chosen[int(pl[0])-1][1]+=1
            elif pl[1]=="2":
                chosen[int(pl[0])-1][2]+=1
            #好了，到这里就是处理完成要导入的角色，可以返回了。
        """
        #这里直接注释掉原来的代码，改成传入处理好的chosen，而chosen的处理交给前端js直接传入JavaScript，然后再后端代码将json转成python所能读取的形式传入函数即可
