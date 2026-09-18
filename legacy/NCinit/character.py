from random import sample,randint
#Characters用于一些初始的角色的模板，其中各个数值代表的意思分别是atk,hp和标签（1重装2迅捷3穿透4治疗）
Characters=[
    ['均衡战士A',8,19,0],
    ['均衡战士B',6,23,0],
    ['均衡战士C',4,26,0],
    ['自爆步兵',16,0,1],
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
        self.team=teami   #战士所属的队伍编号
    #受到攻击的时候调用，作用于对象自身，参数分别为攻击伤害和是否具有穿透标签
    def Hurt(self,gotatk,CureTag,passtag=False,bombtag=False):
        if self.tag == 1 and bombtag == True:
            self.alive=False
            return "两个队伍自爆步兵相杀,均死亡"
        if self.hp<=0:
            raise RuntimeError
        #在这里，我打算效仿一下CureTag的写法用作伤害的转移
        if self.tag==5 and gotatk>=8:
            #铁甲卫士判定
            #重装标签受到大于等于8伤害减少伤害1
            gotatk=(gotatk*6)//10
        elif self.hp<=14 and passtag==True:
            #狂战士判定
            #受到带有狂暴标签的时候伤害乘1.4向下取整
            gotatk=(gotatk*14)//10
        if bombtag==True and self.hp<=24:
            #自爆步兵判定
            gotatk=25
        if self.hp>gotatk:
            #如果一击没死
            self.hp-=gotatk
        elif self.hp<=gotatk:
            #可能死了
            if CureTag[self.team]>0:
                #死灵法师判定
                #如果有治疗，治疗标签消失，血量回3
                self.hp=self.maxhp//2
                CureTag[self.team]-=1
                return (self.name+"被治愈")
            else:
                #毙命判定
                self.hp=0
                self.alive=False
                if self.tag==2:
                    #死灵法师判定，如果死亡则本队伍判定次数归零
                    CureTag[self.team]=0
                return (self.name+"死亡")
        return ""
        #受到攻击之后会返回受攻击报文
    #被call到发出攻击的时候使用，用于判定是否有攻击的资格并且输出伤害和穿透标签，返回伤害和穿透标签
    def Attack(self):
        pstag=False
        bombtag=False
        #预设没有狂暴标签
        if self.alive==False:
            return 0,pstag #已经死亡，类似于打出空击
        #存活
        if self.tag==7:
            #有狂暴标签则赋予
            pstag=True
        if self.tag==1:
            #自爆步兵判定
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
        j = -1
        le=len(self.field[teamn])
        for i in range(le):
            if minhp>self.field[teamn][i].hp and self.field[teamn][i].alive:
                minhp=self.field[teamn][i].hp
                j=i
        return j
    def SoldierInit(self,PlayerSoldierList):
        string_output=""
        #这个playersoldierlist传入的时候是一个列表，里面包含的是几个列表，分别是玩家传入的
        for i in range(self.num):
            #这里的i是队伍的意思
            PLLS=[]
            for j in PlayerSoldierList[i]:
                #这个j理论上就是每一个战士的元组，应当创建Soldier然后传入战场
                Sd=Soldier(j[0],j[2],j[1],j[3],i)
                self.TotalHP[i]+=j[2]
                if Sd.tag==2:
                    #死灵法师判定，如果找到死灵法师那么将治疗次数算入团队中进行统计
                    self.curetag[i]=2
                #这样子，战场里面分别是代表每一个玩家的列表，内部按顺序包含的是每一个角色的
                PLLS.append(Sd)
            self.field.append(PLLS)
            #这里补充一下，我原来的想法是维护一个堆来使得能快速得出血量最低的角色，但是后面考虑到其实如果不使用额外空间而是每次都进行O(n)的比大小也未必不是个好主意，所以最后选择了擂台得出的解法，但是这里维护一个比大小使用的函数方便后期的改变形式
        #判定诅咒巫师
        for i in range(self.num):
            for j in self.field[i]:
                #这里的j应该是已经维护好的soldier，这时候只需要找到那就可以操作对方队伍删除标签了
                if j.tag==3:
                    #诅咒巫师判定
                    Targetgroup=self.GetTargetGroup(i)
                    for k in range(len(self.field[Targetgroup])):
                        #如果对方没有标签或者也是诅咒巫师则跳过
                        if self.field[Targetgroup][k].tag != 0 and self.field[Targetgroup][k].tag != 3 and self.field[Targetgroup][k].tag != 1:
                            self.field[Targetgroup][k].tag=0
                            string_output=string_output+str(i+1)+"组死灵法师消除了对方"+self.field[Targetgroup][k].name+"角色标签\n"
                            break
                            #判定成功则跳出对对方的查找判定
        #先手判定
        if self.FirstTeam==-1:
            if self.TotalHP[0]>self.TotalHP[1]:
                self.FirstTeam=1
            elif self.TotalHP[0]<self.TotalHP[1]:
                self.FirstTeam=0
            else:
                self.FirstTeam=randint(0,1)
            return (string_output+"没有根据迅捷标签决定先手权，根据机制生成先手为{}".format(self.FirstTeam+1))
        else:
            return string_output+("{}队拥有迅捷，先发起了攻击".format(self.FirstTeam+1))


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
                        StringRet=StringRet+("{}的{}已经死亡，没有攻击\n".format(j+1,self.field[j][i].name))
                        #判断角色是否死亡，不拥有攻击能力
                        continue
                    #攻击方产生攻击信息 与受攻击者没有关系
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
        return StringRet,WinTeam #传出的是文本传出信息和胜利队伍的序号，其实我觉得后期改成用特定格式的符号表示比较方便处理（例如转换json）但是暂且这样吧
class Player:
    def __init__(self):
        #创建player不用初始传入参数
        self.ctnum=3 #每次选中的角色数量
        self.poolsize=6 #角色池的大小
        self.ctpool=sample(Characters,self.poolsize) #抽取出角色池
        #预设摇6个选三个
    def GetCharacters(self):
        #一开始想的是包含输入输出，现在看来就是只用返回自己的池子供外部取就行（这样来保证在后续开发中随机池子和池子是保存在后端的）
        return self.ctpool
    def MakeCharacter(self,cc):
        #cc是切片的文本，是一个列表包含选取角色的序号（但是其实里面元素到底是int还是str无所谓因为下面规定了int()
        #这个就是把选中的角色转换成导入的格式放到chosen池子里面罢了
        chosen=[]
        for i in cc:
            chosen.append(self.ctpool[int(i) - 1].copy())
        return chosen